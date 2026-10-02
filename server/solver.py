"""织物组织修补求解器。

把"每行/每列环向相同值连续段长度 <= K，且全同行/列视为无限浮长"
编码为 SAT 硬约束，用 MaxSAT(RC2) 求翻转未锁定格数最少的修补，
再以"按行展开字典序最小"为目标逐格假设求解，得到最终格值序列。

格值约定：0 = 经线在上，1 = 纬线在上。
"""

from pysat.solvers import Solver
from pysat.formula import WCNF
from pysat.examples.rc2 import RC2
from pysat.card import CardEnc, EncType


def _var(r, c, cols):
    """单元 (r, c) 对应的 SAT 变量编号（从 1 开始）。"""
    return r * cols + c + 1


def build_hard_clauses(rows, cols, k, grid, locks):
    """构造硬约束子句。

    - 行约束：对每行、每个环向起点，长度 L = min(K+1, cols) 的窗口
      不允许全 0 或全 1。L == cols 时窗口即整行，恰好表达
      "全同行视为无限浮长"。
    - 列约束：同理，窗口长度 L = min(K+1, rows)。
    - 锁定格：固定为原稿值。
    """
    clauses = []

    row_len = min(k + 1, cols)
    for r in range(rows):
        seen = set()
        for c0 in range(cols):
            cells = tuple(sorted((c0 + i) % cols for i in range(row_len)))
            if cells in seen:
                continue
            seen.add(cells)
            lits = [_var(r, c, cols) for c in cells]
            clauses.append(lits[:])             # 禁止全 0
            clauses.append([-v for v in lits])  # 禁止全 1

    col_len = min(k + 1, rows)
    for c in range(cols):
        seen = set()
        for r0 in range(rows):
            cells = tuple(sorted((r0 + i) % rows for i in range(col_len)))
            if cells in seen:
                continue
            seen.add(cells)
            lits = [_var(r, c, cols) for r in cells]
            clauses.append(lits[:])             # 禁止全 0
            clauses.append([-v for v in lits])  # 禁止全 1

    for (r, c) in locks:
        v = _var(r, c, cols)
        clauses.append([v if grid[r][c] == 1 else -v])

    return clauses


def _flip_lits(rows, cols, grid, locks):
    """每个未锁定格的"翻转指示"文字：为真表示该格被翻转。"""
    locked = set(map(tuple, locks))
    lits = []
    for r in range(rows):
        for c in range(cols):
            if (r, c) in locked:
                continue
            v = _var(r, c, cols)
            lits.append(v if grid[r][c] == 0 else -v)
    return lits


def _at_most(clauses, lits, bound, next_var):
    """Sinz 顺序计数器编码：sum(lits) <= bound，返回 (clauses, 下一空闲变量)。

    next_var 为第一个空闲变量编号（须大于所有已用变量）。
    """
    n = len(lits)
    if bound >= n:
        return clauses, next_var
    if bound == 0:
        for lit in lits:
            clauses.append([-lit])
        return clauses, next_var

    # s[(i, j)]：前 i 个文字中至少 j 个为真（1 <= i <= n, 1 <= j <= bound）
    def s(i, j):
        return next_var + (i - 1) * bound + (j - 1)

    clauses.append([-lits[0], s(1, 1)])
    for j in range(2, bound + 1):
        clauses.append([-s(1, j)])  # 前 1 个不可能 >= j（j>=2）

    for i in range(2, n + 1):
        li = lits[i - 1]
        clauses.append([-li, s(i, 1)])
        clauses.append([-s(i - 1, 1), s(i, 1)])
        for j in range(2, bound + 1):
            clauses.append([-li, -s(i - 1, j - 1), s(i, j)])
            clauses.append([-s(i - 1, j), s(i, j)])
        clauses.append([-li, -s(i - 1, bound)])

    return clauses, next_var + n * bound


def _ones_range_clauses(rows, cols, ones_range, next_var):
    """格值 1 总数区间的硬约束子句：下限 <= sum(格值为1) <= 上限。

    变量取真即该格格值为 1（见 _var）。区间为 None 时不增加约束。
    计数采用 totalizer 基数编码（在 MaxSAT 迭代求解下比顺序计数器更稳，
    16×8 大多数区间亚秒级，个别精确计数硬实例约数秒）。
    两次编码使用互不重叠的辅助变量区间。
    返回 (clauses, 下一空闲变量号 = 已用最大变量 + 1)。
    """
    if ones_range is None:
        return [], next_var
    lower, upper = ones_range
    all_vars = [_var(r, c, cols) for r in range(rows) for c in range(cols)]
    clauses = []
    free = next_var
    # 上限：1 的数量 <= upper（upper == n 时恒成立，无需编码）
    if upper < len(all_vars):
        c1 = CardEnc.atmost(lits=all_vars, bound=upper, top_id=free - 1,
                            encoding=EncType.totalizer)
        clauses.extend(c1.clauses)
        free = c1.nv + 1
    # 下限：1 的数量 >= lower（lower == 0 时恒成立）
    if lower > 0:
        c2 = CardEnc.atleast(lits=all_vars, bound=lower, top_id=free - 1,
                             encoding=EncType.totalizer)
        clauses.extend(c2.clauses)
        free = c2.nv + 1
    return clauses, free


def solve_repair(rows, cols, k, grid, locks, ones_range=None):
    """求最优修补。

    ones_range 给出修补稿格值 1 总数区间 (下限, 上限)，None 表示不限制。
    返回 None 表示无可行修补；否则返回 dict：
    {
        "repaired": 修补后的二维格值,
        "flips":    [[r, c], ...] 被翻转的格（按行展开序）,
    }
    """
    hard = build_hard_clauses(rows, cols, k, grid, locks)
    # 格值 1 总数区间与浮长、锁定同为硬约束；辅助变量从格变量之后开始编号
    range_clauses, next_free = _ones_range_clauses(
        rows, cols, ones_range, rows * cols + 1)
    hard.extend(range_clauses)
    locked = set(map(tuple, locks))

    # 第一阶段：MaxSAT 求最少翻转数
    wcnf = WCNF()
    for cl in hard:
        wcnf.append(cl)
    for r in range(rows):
        for c in range(cols):
            if (r, c) in locked:
                continue
            v = _var(r, c, cols)
            # 软约束：倾向于不翻转（保持原值）
            wcnf.append([-v] if grid[r][c] == 0 else [v], weight=1)

    with RC2(wcnf, solver="g3", adapt=True, exhaust=True) as rc2:
        if rc2.compute() is None:
            return None
        best = rc2.cost

    # 第二阶段：在翻转数 <= best 的约束下，逐格求字典序最小格值序列
    clauses = [cl[:] for cl in hard]
    clauses, _ = _at_most(clauses, _flip_lits(rows, cols, grid, locks),
                          best, next_free)

    with Solver(name="g3", bootstrap_with=clauses) as solver:
        for r in range(rows):
            for c in range(cols):
                if (r, c) in locked:
                    continue
                v = _var(r, c, cols)
                # 字典序最小：优先尝试该格取 0
                if solver.solve(assumptions=[-v]):
                    solver.add_clause([-v])
                else:
                    solver.add_clause([v])
        assert solver.solve(), "翻转数约束下应恒有解"
        model = set(solver.get_model())

    repaired = [[1 if _var(r, c, cols) in model else 0
                 for c in range(cols)] for r in range(rows)]
    flips = [[r, c] for r in range(rows) for c in range(cols)
             if repaired[r][c] != grid[r][c]]
    return {"repaired": repaired, "flips": flips}
