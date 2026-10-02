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

# ============================================================
# 候选比选：最多 3 个同样最优（翻转数同为全局最少）的可行网格
# ============================================================

# DRAFT_A（2x2 [[0,1],[0,1]]）恰有 2 个最优网格，且与当前原稿无关地可枚举
pv = client.post("/api/preview", json=DRAFT_A).get_json()
check("预览带 candidates 与 candidateCount",
      pv["solvable"] and isinstance(pv.get("candidates"), list)
      and pv.get("candidateCount") == len(pv["candidates"])
      and 1 <= len(pv["candidates"]) <= 3,
      json.dumps({k: pv.get(k) for k in
                  ("candidateCount", "candidates")}, ensure_ascii=False)[:300])
check("候选网格互不相同",
      len({json.dumps(c["repaired"]) for c in pv["candidates"]})
      == len(pv["candidates"]))
c0, c1 = pv["candidates"][0], pv["candidates"][1]
check("每个候选带网格/翻转坐标/1总数",
      all(set(c.keys()) >= {"repaired", "flips", "onesCount"}
          for c in pv["candidates"]))
check("候选翻转数同为全局最少且与 flipCount 一致",
      all(len(c["flips"]) == pv["flipCount"] for c in pv["candidates"])
      and pv["flipCount"] == len(c0["flips"]))
check("候选按行展开字典序升序",
      [tuple(v for row in c["repaired"] for v in row)
       for c in pv["candidates"]]
      == sorted(tuple(v for row in c["repaired"] for v in row)
                for c in pv["candidates"]))
check("顶层 repaired/flips/flipCount 始终指向候选 0",
      pv["repaired"] == c0["repaired"] and pv["flips"] == c0["flips"]
      and pv["flipCount"] == len(c0["flips"]))
check("候选 onesCount 与网格一致",
      all(c["onesCount"] == sum(v for row in c["repaired"] for v in row)
          for c in pv["candidates"]))
check("2x2 草稿恰有 2 个最优候选", len(pv["candidates"]) == 2,
      json.dumps([c["repaired"] for c in pv["candidates"]]))

# 不带 choice（旧请求）仍确认首个方案
before_v = get_state()["version"]
r = client.post("/api/confirm", json=payload(DRAFT_A, pv["token"]))
check("旧请求未指定候选 -> 确认首个方案",
      r.status_code == 200 and r.get_json()["state"]["grid"] == c0["repaired"]
      and r.get_json()["state"]["version"] == before_v + 1)

# 重新预览，这次选择候选 1
pv = client.post("/api/preview", json=DRAFT_A).get_json()
r = client.post("/api/confirm",
                json={**payload(DRAFT_A, pv["token"]), "choice": 1})
check("choice=1 确认第二个方案",
      r.status_code == 200 and r.get_json()["state"]["grid"] == c1["repaired"]
      and r.get_json()["state"]["onesCount"] == c1["onesCount"],
      json.dumps(r.get_json())[:200])
# 令牌已消费，候选 0 也不能再选
r = client.post("/api/confirm",
                json={**payload(DRAFT_A, pv["token"]), "choice": 0})
check("已消费令牌再选候选 0 -> 409", r.status_code == 409)

# --- 越界 / 伪造 choice：400 且不消费令牌 ---
pv = client.post("/api/preview", json=DRAFT_A).get_json()
n = pv["candidateCount"]
before = get_state()
for bad_choice in (n, n + 5, -1, 99):
    r = client.post("/api/confirm",
                    json={**payload(DRAFT_A, pv["token"]), "choice": bad_choice})
    check(f"choice={bad_choice} 越界 -> 400", r.status_code == 400,
          r.get_json().get("error", "")[:40])
for bad_choice in ("1", 1.0, True, [1], None):
    body = {**payload(DRAFT_A, pv["token"])}
    if bad_choice is None:
        body.pop("choice", None)  # None 视为缺省合法，单独不测
        continue
    body["choice"] = bad_choice
    r = client.post("/api/confirm", json=body)
    check(f"choice={json.dumps(bad_choice)} 类型非法 -> 400",
          r.status_code == 400)
after = get_state()
check("非法 choice 不消费令牌、原稿版本与内容不变",
      after["version"] == before["version"]
      and after["grid"] == before["grid"])
# 令牌仍可用于合法选择
r = client.post("/api/confirm",
                json={**payload(DRAFT_A, pv["token"]), "choice": 0})
check("非法选择后令牌仍可合法确认候选 0",
      r.status_code == 200
      and r.get_json()["state"]["grid"] == c0["repaired"])

# --- 伪造选择 + 改动草稿：先 409 mismatch（令牌仍未消费）---
pv = client.post("/api/preview", json=DRAFT_A).get_json()
r = client.post("/api/confirm",
                json={**payload(DRAFT_B, pv["token"]), "choice": 1})
check("伪造选择且草稿不一致 -> 409 mismatch", r.status_code == 409)
r = client.post("/api/confirm", json=payload(DRAFT_A, pv["token"]))
check("mismatch 后令牌仍有效，确认首个成功", r.status_code == 200)

# --- 旧版本语义：甲选 1 确认后，乙的旧令牌无论选什么都 409 stale ---
pa = client.post("/api/preview", json=DRAFT_A).get_json()
pb = client.post("/api/preview", json=DRAFT_B).get_json()
r = client.post("/api/confirm",
                json={**payload(DRAFT_A, pa["token"]), "choice": 1})
check("甲选候选1成功", r.status_code == 200
      and r.get_json()["state"]["grid"] == pa["candidates"][1]["repaired"])
for ch in (0, 1):
    r = client.post("/api/confirm",
                    json={**payload(DRAFT_B, pb["token"]), "choice": ch})
    check(f"乙旧令牌 choice={ch} -> 409 stale", r.status_code == 409)
check("乙失败后原稿仍是甲所选候选1",
      get_state()["grid"] == pa["candidates"][1]["repaired"])

# --- 同一令牌并发选不同候选：仅一次生效，原稿只被覆盖一次 ---
pv = client.post("/api/preview", json=DRAFT_A).get_json()
before = get_state()
barrier2 = threading.Barrier(4)


def fire_choice(ch):
    barrier2.wait()
    return client.post("/api/confirm",
                       json={**payload(DRAFT_A, pv["token"]), "choice": ch})


with ThreadPoolExecutor(max_workers=4) as ex:
    futs = [ex.submit(fire_choice, i % 2) for i in range(4)]
    codes = [f.result().status_code for f in futs]
after = get_state()
ok = [code for code in codes if code == 200]
check("4 个并发选择仅 1 个成功", len(ok) == 1, f"results={codes}")
check("并发后版本只递增一次", after["version"] == before["version"] + 1)
check("并发后原稿为两个候选之一（确属本预览）",
      after["grid"] in (c0["repaired"], c1["repaired"]))

# --- 区间 + 候选：每个候选的 1 总数都落在区间内 ---
cur = get_state()
R4 = {"rows": 4, "cols": 4,
      "grid": [[0] * 4 for _ in range(4)], "locks": [], "k": 2}
pr = client.post("/api/preview", json={**R4, "onesRange": [8, 8]}).get_json()
check("区间预览返回多个候选且全部达标",
      pr["solvable"] and pr["candidateCount"] >= 2
      and all(8 <= c["onesCount"] <= 8 for c in pr["candidates"])
      and all(len(c["flips"]) == pr["flipCount"] for c in pr["candidates"]))
r = client.post("/api/confirm",
                json={**R4, "onesRange": [8, 8], "token": pr["token"],
                      "choice": pr["candidateCount"] - 1})
last = pr["candidates"][-1]
check("区间下确认末尾候选成功并持久化区间",
      r.status_code == 200
      and r.get_json()["state"]["grid"] == last["repaired"]
      and r.get_json()["state"]["onesRange"] == [8, 8]
      and r.get_json()["state"]["onesCount"] == last["onesCount"])

# --- 无令牌/无解相关：无解仍不返回 candidates ---
uns = client.post("/api/preview",
                  json=make_draft([[0]], locks=[[0, 0]], k=2)).get_json()
check("无解响应无 candidates 无 token",
      uns["solvable"] is False and "token" not in uns
      and "candidates" not in uns)

# 预览响应仍包含旧字段
pv = client.post("/api/preview", json=DRAFT_A).get_json()
check("预览响应字段向后兼容（含候选字段）",
      {"solvable", "token", "violations", "input", "repaired", "flips",
       "flipCount", "candidates", "candidateCount"}.issubset(pv.keys()))
r = client.post("/api/confirm", json=payload(DRAFT_A, pv["token"]))
check("收尾确认成功", r.status_code == 200)

print()
if FAILS:
    print(f"{len(FAILS)} 项失败：{FAILS}")
    sys.exit(1)
print("全部通过。")
