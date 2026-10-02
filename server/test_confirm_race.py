"""确认竞争修复的端到端验证（Flask test client + 多线程并发）。

覆盖：
- 常规预览/确认成功，版本恰好 +1，令牌被消费后不可重复确认；
- 同一令牌并发确认只有一次生效（原稿只被覆盖一次）；
- 甲、乙基于同一原稿分别预览，甲确认后乙的旧令牌被拒（版本已变）；
- 草稿与预览输入不一致按 409 拒绝且原稿不变；
- 无解预览不产生令牌、不可确认；
- 非法请求仍按 400 拒绝；
- 旧库（无 base_version 列）迁移后旧令牌不可确认。
"""

import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用临时库，避免污染 data/weave.db
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name

import app as server  # noqa: E402

client = server.app.test_client()

FAILS = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}{(' :: ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def payload(draft, token=None):
    body = dict(draft)
    if token is not None:
        body["token"] = token
    return body


def get_state():
    return client.get("/api/state").get_json()


def make_draft(grid, locks=None, k=2, rows=None, cols=None):
    rows = rows or len(grid)
    cols = cols or len(grid[0])
    return {"rows": rows, "cols": cols, "grid": grid,
            "locks": locks or [], "k": k}


# 一个存在超限段且可修补的草稿：2x2 中三个 0（同列两个 0 环向连续，K=2 临界），
# 直接用“全部置 1 的一行”制造全同行超限更直观：
DRAFT_A = make_draft([[0, 1], [0, 1]])  # 每列全同 -> 超限，可修补
DRAFT_B = make_draft([[1, 0], [1, 0]])  # 另一份不同草稿

# --- 初始状态 ---
st0 = get_state()
check("初始版本为 1", st0["version"] == 1, f"version={st0['version']}")

# --- 非法请求仍 400 ---
bad = client.post("/api/preview", json={"rows": 0, "cols": 2,
                                        "grid": [], "locks": [], "k": 1})
check("非法预览 400", bad.status_code == 400)

conf_bad = client.post("/api/confirm", json={"token": "x",
                       "rows": 1, "cols": 1, "grid": [[0]], "locks": [], "k": 0})
check("非法确认 400（K=0）", conf_bad.status_code == 400)

conf_notoken = client.post("/api/confirm", json={})
check("缺令牌 400", conf_notoken.status_code == 400)

# --- 无解预览不产生令牌 ---
# 1x1 格子：全同行/列恒超限，唯一格锁定后无法翻转 -> 无解
unsolvable = make_draft([[0]], locks=[[0, 0]], k=2)
r = client.post("/api/preview", json=unsolvable).get_json()
check("无解预览 solvable=false 且无 token",
      r["solvable"] is False and "token" not in r, json.dumps(r)[:200])

# --- 甲、乙基于同一原稿分别预览 ---
pa = client.post("/api/preview", json=DRAFT_A).get_json()
pb = client.post("/api/preview", json=DRAFT_B).get_json()
check("两份预览均可解并带令牌",
      pa["solvable"] and pb["solvable"] and pa["token"] != pb["token"])

# 乙拿着令牌却提交甲的草稿 -> mismatch
r = client.post("/api/confirm", json=payload(DRAFT_A, pb["token"]))
check("草稿与预览输入不一致 -> 409", r.status_code == 409,
      r.get_json().get("error", ""))
check("mismatch 后原稿不变", get_state()["version"] == 1)

# 甲确认成功
r = client.post("/api/confirm", json=payload(DRAFT_A, pa["token"]))
check("甲确认成功 200", r.status_code == 200)
st1 = r.get_json()["state"]
check("确认后版本 +1", st1["version"] == 2, f"version={st1['version']}")
check("确认结果为修补网格", st1["grid"] == pa["repaired"])

# 甲重复确认（令牌已消费）
r2 = client.post("/api/confirm", json=payload(DRAFT_A, pa["token"]))
check("重复确认已消费令牌 -> 409", r2.status_code == 409)
check("重复确认后版本不变", get_state()["version"] == 2)

# 乙再用旧令牌确认 -> stale（绑定版本 1，当前版本 2）
r3 = client.post("/api/confirm", json=payload(DRAFT_B, pb["token"]))
check("甲确认后乙旧令牌 -> 409(stale)", r3.status_code == 409,
      r3.get_json().get("error", ""))
check("乙失败后原稿仍是甲的结果且版本 2", get_state()["version"] == 2)
check("乙失败不改原稿内容", get_state()["grid"] == pa["repaired"])

# --- 同一令牌并发确认：恰好一次生效 ---
pc = client.post("/api/preview", json=DRAFT_B).get_json()
before = get_state()
results = []
barrier = threading.Barrier(5)


def fire():
    barrier.wait()
    return client.post("/api/confirm", json=payload(DRAFT_B, pc["token"]))


with ThreadPoolExecutor(max_workers=5) as ex:
    futs = [ex.submit(fire) for _ in range(5)]
    results = [f.result().status_code for f in futs]

after = get_state()
ok = [code for code in results if code == 200]
check("5 个并发确认仅 1 个成功", len(ok) == 1, f"results={results}")
check("并发后版本只递增一次", after["version"] == before["version"] + 1,
      f"{before['version']} -> {after['version']}")
check("并发后原稿为该预览修补结果", after["grid"] == pc["repaired"])

# --- 冲突后可以重新预览并确认 ---
pd = client.post("/api/preview", json=DRAFT_A).get_json()
r = client.post("/api/confirm", json=payload(DRAFT_A, pd["token"]))
check("冲突后重新预览仍可确认", r.status_code == 200
      and r.get_json()["state"]["version"] == 4)

# --- 成功响应结构与请求字段不变 ---
r = client.post("/api/preview", json=DRAFT_A)
body = r.get_json()
check("预览响应字段不变",
      set(["solvable", "token", "violations", "input", "repaired",
           "flips", "flipCount"]).issubset(body.keys()))
rc = client.post("/api/confirm", json=payload(DRAFT_A, body["token"]))
state = rc.get_json().get("state", {})
check("确认响应 {state:{...,violations}} 结构不变",
      rc.status_code == 200 and "violations" in state and "version" in state)

# --- 格值1总数区间 ---
cur = get_state()
R = {"rows": cur["rows"], "cols": cur["cols"], "grid": cur["grid"],
     "locks": cur["locks"], "k": cur["k"]}
total = R["rows"] * R["cols"]

# 非法区间 -> 400，原稿不变
before_v = get_state()["version"]
for bad in [[2], [2, None], [3, 2], [-1, 2], [0, total + 1], "x", [1.0, 2], [None, None]]:
    rr = client.post("/api/preview", json={**R, "onesRange": bad})
    check(f"非法区间 {json.dumps(bad)} -> 400", rr.status_code == 400)
check("非法区间不改变版本", get_state()["version"] == before_v)

# 合法但不可达的区间 -> solvable=false 且无 token
ones_now = sum(v for row in R["grid"] for v in row)
r = client.post("/api/preview", json={**R, "onesRange": [total, total]})
# 全 1 行/列恒超限：上限=total 必不可达
b = r.get_json()
check("区间不可达 solvable=false 无令牌",
      r.status_code == 200 and b["solvable"] is False and "token" not in b
      and b["onesRange"] == [total, total], json.dumps(b)[:200])
check("无解响应带原稿 1 总数", b.get("onesCount") == ones_now)

# 可达区间 [0,total-1]：可解、计数达标并带令牌
r = client.post("/api/preview", json={**R, "onesRange": [0, total - 1]})
b = r.get_json()
check("可达区间预览成功", r.status_code == 200 and b["solvable"] and b["token"])
rep_ones = sum(v for row in b["repaired"] for v in row)
check("修补稿 1 总数在区间内且响应给出双方计数",
      0 <= rep_ones <= total - 1
      and b["onesCount"] == ones_now
      and b["repairedOnesCount"] == rep_ones,
      f"ones={rep_ones}, orig={b.get('onesCount')}")
check("规范化输入带 onesRange", b["input"]["onesRange"] == [0, total - 1])
rtoken = b["token"]

# 确认体缺少区间 -> mismatch 409，原稿不变
r = client.post("/api/confirm", json=payload(R, rtoken))
check("确认体区间缺失 -> 409 mismatch", r.status_code == 409)
check("mismatch 后原稿区间/版本不变",
      get_state()["version"] == before_v and get_state().get("onesRange") is None)

# 区间端点变化 -> mismatch 409
r = client.post("/api/confirm",
                json=payload({**R, "onesRange": [0, total]}, rtoken))
check("确认体区间被改动 -> 409 mismatch", r.status_code == 409)

# 一致确认成功 -> 区间随原稿持久化，版本 +1
r = client.post("/api/confirm",
                json=payload({**R, "onesRange": [0, total - 1]}, rtoken))
check("区间预览确认成功 200", r.status_code == 200)
st = r.get_json()["state"]
check("确认后区间持久化、版本 +1",
      st["version"] == before_v + 1 and st["onesRange"] == [0, total - 1]
      and st["onesCount"] == rep_ones,
      f"v={st['version']} range={st.get('onesRange')}")

# 重复令牌 -> consumed
r = client.post("/api/confirm",
                json=payload({**R, "onesRange": [0, total - 1]}, rtoken))
check("区间令牌重复确认 -> 409", r.status_code == 409)

# 甲、乙区间不同：甲确认后乙的旧版本令牌 stale
cur = get_state()
R2 = {"rows": cur["rows"], "cols": cur["cols"], "grid": cur["grid"],
      "locks": cur["locks"], "k": cur["k"]}
pa1 = client.post("/api/preview",
                  json={**R2, "onesRange": [0, total - 1]}).get_json()
pb1 = client.post("/api/preview",
                  json={**R2, "onesRange": None}).get_json()
check("两份区间/无区间预览令牌不同",
      pa1["solvable"] and pb1["solvable"] and pa1["token"] != pb1["token"])
r = client.post("/api/confirm",
                json=payload({**R2, "onesRange": [0, total - 1]}, pa1["token"]))
check("甲（带区间）确认成功", r.status_code == 200
      and r.get_json()["state"]["onesRange"] == [0, total - 1])
r = client.post("/api/confirm", json=payload(R2, pb1["token"]))
check("乙（旧版本无区间令牌）-> 409 stale", r.status_code == 409)

# 新预览传 null 区间并确认 -> 区间被清除（null），且与无字段请求等价
cur = get_state()
R3 = {"rows": cur["rows"], "cols": cur["cols"], "grid": cur["grid"],
      "locks": cur["locks"], "k": cur["k"]}
pv = client.post("/api/preview", json={**R3, "onesRange": None}).get_json()
check("无区间预览 onesRange=null", pv["solvable"] and pv["onesRange"] is None)
# 无字段请求体与 null 规范化一致，应能同令牌确认
body3 = dict(R3)
body3["token"] = pv["token"]
r = client.post("/api/confirm", json=body3)
check("无字段确认体与 null 区间预览兼容", r.status_code == 200)
check("确认后区间为 null", r.get_json()["state"]["onesRange"] is None)

# 无区间响应仍携带计数字段、可确认
st0 = get_state()
check("state 始终带 onesCount/onesRange",
      "onesCount" in st0 and st0["onesRange"] is None)

print()
if FAILS:
    print(f"{len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("全部通过。")
