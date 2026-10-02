"""织物组织超限段（浮长超过 K 的环向连续段）检测。

每行/每列按环向（左右、上下拼接边界相连）找相同值极大连续段，
长度 > K 的段以及全同的行/列（视为无限浮长）即为超限段。
"""


def _runs_cyclic(values):
    """返回环向序列 values 的极大相同值连续段列表。

    每段为下标列表。若全同，返回 None（表示无限浮长）。
    """
    n = len(values)
    if all(v == values[0] for v in values):
        return None
    # 选一个与前一个位置取值不同的起点，保证环向切分点落在段边界上
    start = next(i for i in range(n) if values[i] != values[(i - 1) % n])
    runs, cur = [], [start]
    for step in range(1, n):
        i = (start + step) % n
        if values[i] == values[cur[-1]]:
            cur.append(i)
        else:
            runs.append(cur)
            cur = [i]
    runs.append(cur)
    return runs


def find_violations(grid, rows, cols, k):
    """找出所有超限段，返回列表。每段形如：
    {"kind": "row"|"col", "index": 行/列号, "value": 0|1,
     "length": 段长或 None（无限）, "cells": [[r, c], ...]}
    """
    violations = []

    for r in range(rows):
        row = grid[r]
        runs = _runs_cyclic(row)
        if runs is None:
            violations.append({
                "kind": "row", "index": r, "value": row[0],
                "length": None,
                "cells": [[r, c] for c in range(cols)],
            })
            continue
        for run in runs:
            if len(run) > k:
                violations.append({
                    "kind": "row", "index": r, "value": row[run[0]],
                    "length": len(run),
                    "cells": [[r, c] for c in run],
                })

    for c in range(cols):
        col = [grid[r][c] for r in range(rows)]
        runs = _runs_cyclic(col)
        if runs is None:
            violations.append({
                "kind": "col", "index": c, "value": col[0],
                "length": None,
                "cells": [[r, c] for r in range(rows)],
            })
            continue
        for run in runs:
            if len(run) > k:
                violations.append({
                    "kind": "col", "index": c, "value": col[run[0]],
                    "length": len(run),
                    "cells": [[r, c] for r in run],
                })

    return violations
