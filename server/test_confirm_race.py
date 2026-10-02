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

# --- 前 3 个同样最优候选的预览/确认比选 ---
# 2x2 全0 K=2：恰有 2 个并列最优（两个对角方向，各翻转 2 格）
TIE2 = make_draft([[0, 0], [0, 0]], k=2)
pv = client.post("/api/preview", json=TIE2).get_json()
check("并列预览可解并带令牌", pv["solvable"] and bool(pv["token"]))
check("candidateCount=2 且候选互不相同",
      pv["candidateCount"] == 2 and len(pv["candidates"]) == 2
      and pv["candidates"][0]["repaired"] != pv["candidates"][1]["repaired"],
      json.dumps(pv.get("candidates")))
c0, c1 = pv["candidates"]
check("旧字段始终指向首个方案",
      pv["repaired"] == c0["repaired"] and pv["flips"] == c0["flips"]
      and pv["flipCount"] == len(c0["flips"]))
check("候选按行展开字典序升序且翻转数相同",
      (tuple(v for row in c0["repaired"] for v in row)
       < tuple(v for row in c1["repaired"] for v in row))
      and len(c0["flips"]) == len(c1["flips"]) == 2
      and c0["onesCount"] == c1["onesCount"] == 2)
before_v = get_state()["version"]

# 越界/伪造候选 -> 400，不消费令牌、原稿不变
for bad_cand in [-1, 2, 5, "1", 1.0, True, False, [0], 99]:
    rr = client.post("/api/confirm",
                     json=payload(TIE2, pv["token"]) | {"candidate": bad_cand})
    check(f"非法候选 {json.dumps(bad_cand)} -> 400", rr.status_code == 400,
          rr.get_json().get("error", "") if rr.is_json else "")
check("伪造候选不消费令牌、原稿不变",
      get_state()["version"] == before_v
      and get_state()["grid"] != c1["repaired"])

# 选择第 2 个候选确认成功，覆盖为候选1网格，版本+1
rr = client.post("/api/confirm",
                 json=payload(TIE2, pv["token"]) | {"candidate": 1})
check("候选1确认成功 200", rr.status_code == 200)
st = rr.get_json()["state"]
check("确认覆盖为选中候选网格", st["grid"] == c1["repaired"]
      and st["grid"] != c0["repaired"])
check("选中确认后版本 +1", st["version"] == before_v + 1,
      f"v={st['version']}")
# 令牌已消费，再选任何候选都 409 consumed
rr = client.post("/api/confirm",
                 json=payload(TIE2, pv["token"]) | {"candidate": 0})
check("消费后再确认 -> 409", rr.status_code == 409)

# 旧请求不带 candidate（及显式 null/0）仍确认首个方案
for extra in ({}, {"candidate": None}, {"candidate": 0}):
    p = client.post("/api/preview", json=TIE2).get_json()
    rr = client.post("/api/confirm", json=payload(TIE2, p["token"]) | extra)
    check(f"省略/空候选 {json.dumps(extra)} 确认首个方案",
          rr.status_code == 200
          and rr.get_json()["state"]["grid"] == p["candidates"][0]["repaired"])

# 3x3 全0 K=2：6 个并列，只返回前 3；第 4 个序号越界不可确认
TIE3 = make_draft([[0] * 3 for _ in range(3)], k=2)
p3 = client.post("/api/preview", json=TIE3).get_json()
check("最多枚举 3 个候选", p3["candidateCount"] == 3
      and len(p3["candidates"]) == 3)
seqs = [tuple(v for row in c["repaired"] for v in row) for c in p3["candidates"]]
check("3 候选互不相同且按字典序",
      len(set(seqs)) == 3 and seqs == sorted(seqs))
rr = client.post("/api/confirm",
                 json=payload(TIE3, p3["token"]) | {"candidate": 3})
check("第 4 序号越界 -> 400 且不消费", rr.status_code == 400)
rr = client.post("/api/confirm",
                 json=payload(TIE3, p3["token"]) | {"candidate": 2})
check("第 3 候选确认成功", rr.status_code == 200
      and rr.get_json()["state"]["grid"] == p3["candidates"][2]["repaired"])

# 解唯一时候选数为 1
UNIQ = make_draft([[0, 1], [1, 0]], k=1)
pu = client.post("/api/preview", json=UNIQ).get_json()
check("唯一解候选数为 1", pu["solvable"] and pu["candidateCount"] == 1
      and len(pu["candidates"]) == 1)

# 候选序号同样受草稿一致性/版本校验：合法序号但草稿改动仍是 mismatch 409
p = client.post("/api/preview", json=TIE2).get_json()
changed = dict(TIE2)
changed["k"] = 3
rr = client.post("/api/confirm", json=payload(changed, p["token"]) | {"candidate": 1})
check("候选合法但草稿改动 -> 409 mismatch", rr.status_code == 409)
check("mismatch 不消费令牌",
      client.post("/api/confirm",
                  json=payload(TIE2, p["token"]) | {"candidate": 0}).status_code
      == 200)

# 同一令牌携带不同候选并发确认：仅一次生效且只应用一个候选网格
pp = client.post("/api/preview", json=TIE3).get_json()
before = get_state()
barrier2 = threading.Barrier(4)


def fire_cand(idx):
    barrier2.wait()
    return client.post("/api/confirm",
                       json=payload(TIE3, pp["token"]) | {"candidate": idx})


with ThreadPoolExecutor(max_workers=4) as ex:
    futs = [ex.submit(fire_cand, i) for i in range(4)]  # idx 3 为越界
    presults = [f.result() for f in futs]
codes = [r.status_code for r in presults]
after = get_state()
ok200 = [r for r in presults if r.status_code == 200]
check("并发候选确认仅 1 个 200（越界为 400，其余 409）",
      len(ok200) == 1 and codes.count(400) == 1 and codes.count(409) == 2,
      f"codes={codes}")
check("并发后版本只 +1", after["version"] == before["version"] + 1)
applied = ok200[0].get_json()["state"]["grid"]
check("生效的正是所确认的候选网格之一且落到原稿",
      applied in [cand["repaired"] for cand in pp["candidates"]]
      and after["grid"] == applied)

# --- 旧版预览（result_json 无 candidates）向后兼容：仅可确认首个方案 ---
import uuid as _uuid
import json as _json
legacy_token = _uuid.uuid4().hex
legacy_input = _json.dumps(
    {"rows": TIE2["rows"], "cols": TIE2["cols"], "grid": TIE2["grid"],
     "locks": [], "k": TIE2["k"], "onesRange": None},
    sort_keys=True, separators=(",", ":"))
legacy_result = {"repaired": c0["repaired"], "flips": c0["flips"],
                 "flipCount": 2}
server.db.save_preview(legacy_token, legacy_input, legacy_result,
                       get_state()["version"])
rr = client.post("/api/confirm",
                 json=payload(TIE2, legacy_token) | {"candidate": 1})
check("旧预览指定候选1 -> 400（旧预览仅有首个方案）", rr.status_code == 400)
rr = client.post("/api/confirm", json=payload(TIE2, legacy_token))
check("旧预览不带候选仍确认首个方案", rr.status_code == 200
      and rr.get_json()["state"]["grid"] == c0["repaired"])

print()
if FAILS:
    print(f"{len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("全部通过。")
