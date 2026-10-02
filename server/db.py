"""SQLite 持久化：当前原稿 + 待确认的预览。

预览在生成时绑定当时的原稿版本；确认在单条事务内原子地完成：
令牌未消费、草稿与预览输入一致、原稿版本未变三个条件全部满足时，
才覆盖原稿（版本恰好 +1）并消费令牌，任一条件不满足则整体回滚。
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS design (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    rows       INTEGER NOT NULL,
    cols       INTEGER NOT NULL,
    grid       TEXT NOT NULL,
    locks      TEXT NOT NULL,
    k          INTEGER NOT NULL,
    ones_range TEXT,
    version    INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preview (
    token        TEXT PRIMARY KEY,
    input_text   TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);
"""

# 默认原稿：4x4 交错平纹，无锁定，K=2，不指定格值1总数区间
DEFAULT_DESIGN = {
    "rows": 4,
    "cols": 4,
    "grid": [[0, 1, 0, 1],
             [1, 0, 1, 0],
             [0, 1, 0, 1],
             [1, 0, 1, 0]],
    "locks": [],
    "k": 2,
    "onesRange": None,
}

# 确认失败的原子结果：(设计稿, None) 成功；(None, 原因) 冲突
# bad_candidate：选择了该令牌预览候选之外的序号（越界/伪造），
# 属于客户端错误（400），同样回滚、不消费令牌、原稿不变。
CONSUME_REASONS = ("consumed", "mismatch", "stale", "bad_candidate")


def _now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self):
        # isolation_level=None：事务边界完全由 BEGIN/COMMIT 显式控制，
        # 保证确认的校验与写入在同一条写事务中原子完成（也能跨进程互斥）。
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_schema(self):
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)
            # 旧库迁移：补齐 base_version 列（旧预览无版本绑定，置空后不可确认）
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(preview)")}
            if "base_version" not in cols:
                conn.execute("ALTER TABLE preview RENAME TO preview_old")
                conn.execute(
                    "CREATE TABLE preview (token TEXT PRIMARY KEY, input_text"
                    " TEXT NOT NULL, result_json TEXT NOT NULL, base_version"
                    " INTEGER, created_at TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO preview (token, input_text, result_json,"
                    " base_version, created_at) SELECT token, input_json,"
                    " result_json, NULL, created_at FROM preview_old")
                conn.execute("DROP TABLE preview_old")
            # 旧库迁移：原稿补齐格值1总数区间列（旧稿无区间，置 NULL）
            dcols = {r["name"] for r in conn.execute("PRAGMA table_info(design)")}
            if "ones_range" not in dcols:
                conn.execute("ALTER TABLE design ADD COLUMN ones_range TEXT")
            row = conn.execute("SELECT COUNT(*) AS n FROM design").fetchone()
            if row["n"] == 0:
                conn.execute(
                    "INSERT INTO design (id, rows, cols, grid, locks, k,"
                    " ones_range, version, updated_at)"
                    " VALUES (1, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (DEFAULT_DESIGN["rows"], DEFAULT_DESIGN["cols"],
                     json.dumps(DEFAULT_DESIGN["grid"]),
                     json.dumps(DEFAULT_DESIGN["locks"]),
                     DEFAULT_DESIGN["k"],
                     None if DEFAULT_DESIGN["onesRange"] is None
                     else json.dumps(DEFAULT_DESIGN["onesRange"]),
                     _now()),
                )

    @staticmethod
    def _row_to_design(row):
        raw_range = row["ones_range"]
        ones_range = None if raw_range is None else json.loads(raw_range)
        return {
            "rows": row["rows"],
            "cols": row["cols"],
            "grid": json.loads(row["grid"]),
            "locks": json.loads(row["locks"]),
            "k": row["k"],
            "onesRange": ones_range,
            "version": row["version"],
        }

    def get_design(self):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM design WHERE id = 1").fetchone()
            return self._row_to_design(row)

    def save_preview(self, token, input_text, result_obj, base_version):
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO preview (token, input_text,"
                " result_json, base_version, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (token, input_text, json.dumps(result_obj), base_version, _now()),
            )

    def commit_preview(self, token, input_text, candidate_index=0):
        """原子确认预览。

        candidate_index 选择该令牌预览中枚举到的并列最优候选
        （0 = 字典序最小，也是旧请求/旧预览的默认值）；序号必须落在
        预览实际返回的候选范围内，否则按 bad_candidate 回滚、
        不消费令牌。

        成功返回 (design, None)：在同一事务内校验并执行
            1. 令牌存在且未消费（DELETE 命中 1 行）；
            2. 提交草稿与预览输入一致（input_text 逐字节相同）；
            3. 预览绑定的原稿版本与当前原稿一致（未被他人确认覆盖）；
            4. candidate_index 是该预览中实际存在的候选。
        冲突时全部回滚、原稿不变，返回 (None, 原因)。
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                prow = conn.execute(
                    "SELECT input_text, result_json, base_version"
                    " FROM preview WHERE token = ?",
                    (token,),
                ).fetchone()
                if prow is None:
                    conn.execute("ROLLBACK")
                    return None, "consumed"
                if prow["input_text"] != input_text:
                    conn.execute("ROLLBACK")
                    return None, "mismatch"

                result = json.loads(prow["result_json"])
                candidates = result.get("candidates")
                if candidates is None:
                    # 旧版预览（result_json 中无 candidates）：仅有首个方案
                    candidates = [{
                        "repaired": result["repaired"],
                        "flips": result.get("flips", []),
                    }]
                if not isinstance(candidate_index, int) \
                        or not 0 <= candidate_index < len(candidates):
                    conn.execute("ROLLBACK")
                    return None, "bad_candidate"
                selected = candidates[candidate_index]

                drow = conn.execute(
                    "SELECT * FROM design WHERE id = 1").fetchone()
                if prow["base_version"] != drow["version"]:
                    conn.execute("ROLLBACK")
                    return None, "stale"

                grid = selected["repaired"]
                input_obj = json.loads(input_text)
                rows, cols, locks, k = (
                    input_obj["rows"], input_obj["cols"],
                    input_obj["locks"], input_obj["k"])
                # 旧版预览（规范化文本中无 onesRange）按未填区间处理
                ones_range = input_obj.get("onesRange")
                conn.execute(
                    "UPDATE design SET rows = ?, cols = ?, grid = ?, locks = ?,"
                    " k = ?, ones_range = ?, version = version + 1,"
                    " updated_at = ? WHERE id = 1",
                    (rows, cols, json.dumps(grid), json.dumps(locks),
                     k, None if ones_range is None else json.dumps(ones_range),
                     _now()),
                )
                # 仅在条件全部满足时消费令牌；UPDATE 后删除，行锁持有到提交
                deleted = conn.execute(
                    "DELETE FROM preview WHERE token = ?", (token,)).rowcount
                if deleted != 1:
                    conn.execute("ROLLBACK")
                    return None, "consumed"
                drow = conn.execute(
                    "SELECT * FROM design WHERE id = 1").fetchone()
                conn.execute("COMMIT")
                return self._row_to_design(drow), None
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()
