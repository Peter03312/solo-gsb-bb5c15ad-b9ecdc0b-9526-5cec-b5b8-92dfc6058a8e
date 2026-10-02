"""求解器与超限段检测的测试（含暴力枚举对照）。"""

import itertools
import random
import sys
import time

from solver import solve_repair, solve_repair_candidates
from weave import find_violations


def brute_force(rows, cols, k, grid, locks):
    """暴力枚举所有未锁定格取值，返回 (最少翻转数, 字典序最小展开序列) 或 None。"""
    locked = set(map(tuple, locks))
    free = [(r, c) for r in range(rows) for c in range(cols)
            if (r, c) not in locked]
    best = None
    for bits in itertools.product((0, 1), repeat=len(free)):
        g = [row[:] for row in grid]
        for (r, c), b in zip(free, bits):
            g[r][c] = b
        if find_violations(g, rows, cols, k):
            continue
        flips = sum(b != grid[r][c] for (r, c), b in zip(free, bits))
        flat = tuple(v for row in g for v in row)
        if best is None or (flips, flat) < (best[0], best[1]):
            best = (flips, flat)
    return best


def flat(grid):
    return tuple(v for row in grid for v in row)


def check_case(rows, cols, k, grid, locks, label):
    expected = brute_force(rows, cols, k, grid, locks)
    got = solve_repair(rows, cols, k, grid, locks)
    if expected is None:
        assert got is None, f"{label}: 应无解但解出 {got}"
        print(f"  [OK] {label}: 无可行修补（与暴力一致）")
        return
    assert got is not None, f"{label}: 应有解但报无解"
    assert len(got["flips"]) == expected[0], (
        f"{label}: 翻转数 {len(got['flips'])} != 最优 {expected[0]}")
    assert flat(got["repaired"]) == expected[1], (
        f"{label}: 字典序不一致\n  got {flat(got['repaired'])}\n  exp {expected[1]}")
    # 解必须合法且尊重锁定
    assert not find_violations(got["repaired"], rows, cols, k)
    for (r, c) in locks:
        assert got["repaired"][r][c] == grid[r][c]
    print(f"  [OK] {label}: 翻转 {expected[0]} 格（与暴力一致）")


def main():
    print("== 固定用例 ==")
    check_case(2, 2, 1, [[0, 0], [0, 0]], [], "2x2 全0 K=1")
    check_case(2, 2, 1, [[0, 0], [0, 0]], [[0, 0], [0, 1]], "2x2 行锁定 -> 无解")
    check_case(4, 4, 1, [[0, 1, 0, 1], [1, 0, 1, 0],
                         [0, 1, 0, 1], [1, 0, 1, 0]], [], "4x4 平纹 K=1 已合法")
    check_case(4, 4, 2, [[0] * 4 for _ in range(4)], [], "4x4 全0 K=2")
    check_case(4, 4, 2, [[0] * 4 for _ in range(4)],
               [[0, 0], [1, 1], [2, 2], [3, 3]], "4x4 全0 K=2 对角锁定")
    check_case(1, 1, 1, [[0]], [], "1x1 必无解")
    check_case(1, 3, 1, [[0, 1, 0]], [], "1x3 必无解（列全同）")
    check_case(3, 1, 1, [[0], [1], [0]], [], "3x1 必无解（行全同）")
    check_case(4, 4, 100, [[0, 1, 0, 1], [1, 0, 1, 0],
                           [0, 1, 0, 1], [1, 0, 1, 0]], [], "K 极大时已合法")
    check_case(3, 3, 1, [[0, 1, 0], [1, 0, 1], [0, 1, 0]], [], "3x3 K=1 奇数环")

    print("== 格值1总数区间 ==")

    def brute_range(rows, cols, k, grid, locks, lo, hi):
        """暴力：合法浮长 + 1 总数落在 [lo, hi]，最少翻转 + 字典序。"""
        locked = set(map(tuple, locks))
        free = [(r, c) for r in range(rows) for c in range(cols)
                if (r, c) not in locked]
        best = None
        for bits in itertools.product((0, 1), repeat=len(free)):
            g = [row[:] for row in grid]
            for (r, c), b in zip(free, bits):
                g[r][c] = b
            if find_violations(g, rows, cols, k):
                continue
            ones = sum(v for row in g for v in row)
            if not (lo <= ones <= hi):
                continue
            flips = sum(b != grid[r][c] for (r, c), b in zip(free, bits))
            seq = tuple(v for row in g for v in row)
            if best is None or (flips, seq) < (best[0], best[1]):
                best = (flips, seq)
        return best

    def check_range(rows, cols, k, grid, locks, lo, hi, label):
        expected = brute_range(rows, cols, k, grid, locks, lo, hi)
        got = solve_repair(rows, cols, k, grid, locks, (lo, hi))
        if expected is None:
            assert got is None, f"{label}: 应无解但解出 {got}"
            print(f"  [OK] {label}: 无达标修补（与暴力一致）")
            return
        assert got is not None, f"{label}: 应有解但报无解"
        seq = tuple(v for row in got["repaired"] for v in row)
        ones = sum(seq)
        assert lo <= ones <= hi, f"{label}: 总数 {ones} 越出 [{lo},{hi}]"
        assert (len(got["flips"]), seq) == expected, (
            f"{label}: {(len(got['flips']), seq)} != {expected}")
        print(f"  [OK] {label}: 1 总数 {ones} ∈ [{lo},{hi}]，翻转 {expected[0]} 格")

    # 4x4 K=2 合法行必恰含 2 个 1，故总数恒为 8
    check_range(4, 4, 2, [[0] * 4 for _ in range(4)], [], 6, 10,
                "4x4 全0 K=2 区间[6,10]")
    check_range(4, 4, 2, [[0] * 4 for _ in range(4)], [], 8, 8,
                "4x4 全0 K=2 恰好8")
    check_range(4, 4, 2, [[0] * 4 for _ in range(4)], [], 9, 12,
                "4x4 全0 K=2 区间[9,12] 无解")
    check_range(4, 4, 2, [[0] * 4 for _ in range(4)], [], 0, 0,
                "4x4 全0 K=2 区间[0,0] 无解（全同列）")
    check_range(4, 4, 2, [[0] * 4 for _ in range(4)], [], 16, 16,
                "4x4 全0 K=2 区间[16,16] 无解（全同行）")
    # 平纹本身合法且含 8 个 1
    plain = [[0, 1, 0, 1], [1, 0, 1, 0], [0, 1, 0, 1], [1, 0, 1, 0]]
    check_range(4, 4, 1, plain, [], 0, 16, "平纹 宽松区间 等价无限制")
    check_range(4, 4, 1, plain, [], 0, 7, "平纹 区间过紧 无解")
    # 锁定与区间冲突
    check_range(2, 2, 2, [[0] * 2 for _ in range(2)],
                [[0, 0], [1, 1]], 3, 4, "锁两0又要求>=3个1 无解")

    print("== 区间随机小网格对照暴力 ==")
    rrng = random.Random(2026)
    for i in range(30):
        rows = rrng.randint(2, 4)
        cols = rrng.randint(2, 4)
        k = rrng.randint(1, 4)
        grid = [[rrng.randint(0, 1) for _ in range(cols)] for _ in range(rows)]
        locks = [[r, c] for r in range(rows) for c in range(cols)
                 if rrng.random() < 0.2]
        lo = rrng.randint(0, rows * cols)
        hi = rrng.randint(lo, rows * cols)
        check_range(rows, cols, k, grid, locks, lo, hi,
                    f"区间随机#{i} {rows}x{cols} K={k} [{lo},{hi}]")

    print("== 超限段检测 ==")
    # 环向跨界：0 的段 {2,0} 长度为 2
    v = find_violations([[0, 1, 0]], 1, 3, 1)
    row_segs = [s for s in v if s["kind"] == "row"]
    assert len(row_segs) == 1 and sorted(map(tuple, row_segs[0]["cells"])) == [(0, 0), (0, 2)], v
    assert len([s for s in v if s["kind"] == "col"]) == 3  # 单列全同=无限
    # 全同行 -> 无限
    v = find_violations([[1, 1, 1]], 1, 3, 5)
    assert any(s["length"] is None for s in v if s["kind"] == "row")
    # 合法平纹无超限
    assert not find_violations([[0, 1], [1, 0]], 2, 2, 1)
    print("  [OK] 超限段检测（含跨界、全同、合法）")

    print("== 随机小网格对照暴力 ==")
    rng = random.Random(42)
    for i in range(60):
        rows = rng.randint(2, 5)
        cols = rng.randint(2, 4)
        k = rng.randint(1, 4)
        grid = [[rng.randint(0, 1) for _ in range(cols)] for _ in range(rows)]
        locks = [[r, c] for r in range(rows) for c in range(cols)
                 if rng.random() < 0.25]
        check_case(rows, cols, k, grid, locks,
                   f"随机#{i} {rows}x{cols} K={k} 锁{len(locks)}")

    print("== 候选比选（同样最优的前 3 个互不相同网格）==")

    def brute_optimal_grids(rows, cols, k, grid, locks, lo=None, hi=None):
        """暴力：返回全部最优网格的有序列表 (翻转数, [行展开 tuple, ...])。

        合法（含区间）且翻转数最少的网格，按行展开字典序升序。
        """
        locked = set(map(tuple, locks))
        free = [(r, c) for r in range(rows) for c in range(cols)
                if (r, c) not in locked]
        best_n = None
        grids = []
        for bits in itertools.product((0, 1), repeat=len(free)):
            g = [row[:] for row in grid]
            for (r, c), b in zip(free, bits):
                g[r][c] = b
            if find_violations(g, rows, cols, k):
                continue
            ones = sum(v for row in g for v in row)
            if lo is not None and not (lo <= ones <= hi):
                continue
            flips = sum(b != grid[r][c] for (r, c), b in zip(free, bits))
            if best_n is None or flips < best_n:
                best_n, grids = flips, [tuple(v for row in g for v in row)]
            elif flips == best_n:
                grids.append(tuple(v for row in g for v in row))
        if best_n is None:
            return None
        grids.sort()
        return best_n, grids

    def check_candidates(rows, cols, k, grid, locks, lo=None, hi=None,
                         label=""):
        ones_range = None if lo is None else (lo, hi)
        expected = brute_optimal_grids(rows, cols, k, grid, locks, lo, hi)
        got = solve_repair_candidates(
            rows, cols, k, grid, locks, ones_range)
        if expected is None:
            assert got is None, f"{label}: 应无解但解出 {got}"
            print(f"  [OK] {label}: 无可行修补（与暴力一致）")
            return
        assert got is not None, f"{label}: 应有解但报无解"
        best_n, all_grids = expected
        exp = all_grids[:3]
        assert got["flipCount"] == best_n, (
            f"{label}: 最少翻转 {got['flipCount']} != {best_n}")
        got_seqs = [tuple(v for row in c["repaired"] for v in row)
                    for c in got["candidates"]]
        assert got_seqs == exp, (
            f"{label}: 候选序列不一致\n  got {got_seqs}\n  exp {exp}")
        # 每个候选字段自洽、翻转数同为全局最少、网格互不相同
        assert len(set(got_seqs)) == len(got_seqs)
        assert 1 <= len(got_seqs) <= 3
        for c in got["candidates"]:
            assert len(c["flips"]) == best_n
            assert c["onesCount"] == sum(c["repaired"][r][cc]
                                         for r in range(rows)
                                         for cc in range(cols))
            assert not find_violations(c["repaired"], rows, cols, k)
            if lo is not None:
                assert lo <= c["onesCount"] <= hi
        # 单解接口恒与候选 0 一致
        single = solve_repair(rows, cols, k, grid, locks, ones_range)
        assert tuple(v for row in single["repaired"] for v in row) == exp[0]
        print(f"  [OK] {label}: 最优共 {len(all_grids)} 个网格，"
              f"返回 {len(exp)} 个，翻转 {best_n} 格")

    check_candidates(2, 2, 2, [[0, 1], [0, 1]], [], label="2x2 列全同 2 个最优")
    check_candidates(4, 4, 2, [[0] * 4 for _ in range(4)], [],
                     label="4x4 全0 K=2 前3")
    check_candidates(4, 4, 2, [[0] * 4 for _ in range(4)], [], 8, 8,
                     label="4x4 全0 K=2 区间[8,8]")
    check_candidates(4, 4, 1,
                     [[0, 1, 0, 1], [1, 0, 1, 0],
                      [0, 1, 0, 1], [1, 0, 1, 0]], [],
                     label="平纹唯一最优（0 翻转）")
    check_candidates(2, 2, 2, [[0] * 2 for _ in range(2)],
                     [[0, 0], [1, 1]], label="锁对角仅2格自由")
    check_candidates(4, 4, 2, [[0] * 4 for _ in range(4)], [], 9, 12,
                     label="区间无解")

    crng = random.Random(7)
    for i in range(40):
        rows = crng.randint(2, 4)
        cols = crng.randint(2, 4)
        k = crng.randint(1, 4)
        grid = [[crng.randint(0, 1) for _ in range(cols)] for _ in range(rows)]
        locks = [[r, c] for r in range(rows) for c in range(cols)
                 if crng.random() < 0.25]
        if crng.random() < 0.6:
            lo = crng.randint(0, rows * cols)
            hi = crng.randint(lo, rows * cols)
        else:
            lo = hi = None
        check_candidates(rows, cols, k, grid, locks, lo, hi,
                         f"候选随机#{i} {rows}x{cols} K={k}"
                         + ("" if lo is None else f" [{lo},{hi}]"))

    print("== 大网格性能 ==")
    for k in (1, 2, 3, 5, 8, 16, 1000):
        rows, cols = 16, 8
        grid = [[rng.randint(0, 1) for _ in range(cols)] for _ in range(rows)]
        t0 = time.time()
        got = solve_repair(rows, cols, k, grid, [])
        dt = time.time() - t0
        assert got is not None
        assert not find_violations(got["repaired"], rows, cols, k)
        print(f"  [OK] 16x8 K={k}: 翻转 {len(got['flips'])} 格, {dt:.2f}s")
        assert dt < 10, "求解超时"

    # 大量锁定 + 大网格
    grid = [[rng.randint(0, 1) for _ in range(8)] for _ in range(16)]
    locks = [[r, c] for r in range(16) for c in range(8) if rng.random() < 0.5]
    t0 = time.time()
    got = solve_repair(16, 8, 3, grid, locks)
    dt = time.time() - t0
    if got is not None:
        assert not find_violations(got["repaired"], 16, 8, 3)
        for (r, c) in locks:
            assert got["repaired"][r][c] == grid[r][c]
        print(f"  [OK] 16x8 K=3 半锁定: 翻转 {len(got['flips'])} 格, {dt:.2f}s")
    else:
        print(f"  [OK] 16x8 K=3 半锁定: 无解, {dt:.2f}s")

    # 16x8 精确格值1总数（totalizer 编码性能）
    prng = random.Random(99)
    for (lo, hi) in [(0, 128), (32, 96), (64, 64), (60, 68), (0, 10)]:
        pgrid = [[prng.randint(0, 1) for _ in range(8)] for _ in range(16)]
        t0 = time.time()
        pgot = solve_repair(16, 8, 3, pgrid, [], (lo, hi))
        dt = time.time() - t0
        if pgot is not None:
            ones = sum(v for row in pgot["repaired"] for v in row)
            assert lo <= ones <= hi
            assert not find_violations(pgot["repaired"], 16, 8, 3)
            print(f"  [OK] 16x8 K=3 区间[{lo},{hi}]: 1总数 {ones}, "
                  f"翻转 {len(pgot['flips'])} 格, {dt:.2f}s")
        else:
            print(f"  [OK] 16x8 K=3 区间[{lo},{hi}]: 无解, {dt:.2f}s")
        assert dt < 10, "区间求解超时"

    # 候选枚举（3 个）在硬实例上仍不超过单次 MaxSAT 耗时太多
    prng2 = random.Random(99)
    cgrid = [[prng2.randint(0, 1) for _ in range(8)] for _ in range(16)]
    t0 = time.time()
    cgot = solve_repair_candidates(16, 8, 3, cgrid, [], (64, 64))
    dt = time.time() - t0
    assert cgot is not None and len(cgot["candidates"]) == 3
    assert all(len(c["flips"]) == cgot["flipCount"] for c in cgot["candidates"])
    print(f"  [OK] 16x8 K=3 精确区间取3候选: {dt:.2f}s")
    assert dt < 15, "候选枚举超时"

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
