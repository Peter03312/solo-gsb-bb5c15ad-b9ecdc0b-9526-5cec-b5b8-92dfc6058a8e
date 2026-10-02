// 超限段检测：与后端 weave.py 逻辑一致。
// 每行/每列按环向（左右、上下拼接边界相连）找相同值极大连续段，
// 长度 > K 的段以及全同的行/列（视为无限浮长）即为超限段。

function runsCyclic(values) {
  const n = values.length;
  if (values.every((v) => v === values[0])) return null; // 全同：无限浮长
  // 选一个与前一个位置取值不同的起点，保证环向切分点落在段边界上
  let start = 0;
  for (let i = 0; i < n; i += 1) {
    if (values[i] !== values[(i - 1 + n) % n]) { start = i; break; }
  }
  const runs = [];
  let cur = [start];
  for (let step = 1; step < n; step += 1) {
    const i = (start + step) % n;
    if (values[i] === values[cur[cur.length - 1]]) {
      cur.push(i);
    } else {
      runs.push(cur);
      cur = [i];
    }
  }
  runs.push(cur);
  return runs;
}

export function findViolations(grid, rows, cols, k) {
  const violations = [];

  for (let r = 0; r < rows; r += 1) {
    const row = grid[r];
    const runs = runsCyclic(row);
    if (runs === null) {
      violations.push({
        kind: "row", index: r, value: row[0], length: null,
        cells: Array.from({ length: cols }, (_, c) => [r, c]),
      });
      continue;
    }
    for (const run of runs) {
      if (run.length > k) {
        violations.push({
          kind: "row", index: r, value: row[run[0]], length: run.length,
          cells: run.map((c) => [r, c]),
        });
      }
    }
  }

  for (let c = 0; c < cols; c += 1) {
    const col = Array.from({ length: rows }, (_, r) => grid[r][c]);
    const runs = runsCyclic(col);
    if (runs === null) {
      violations.push({
        kind: "col", index: c, value: col[0], length: null,
        cells: Array.from({ length: rows }, (_, r) => [r, c]),
      });
      continue;
    }
    for (const run of runs) {
      if (run.length > k) {
        violations.push({
          kind: "col", index: c, value: col[run[0]], length: run.length,
          cells: run.map((r) => [r, c]),
        });
      }
    }
  }

  return violations;
}

export function violationCellSet(violations) {
  const set = new Set();
  for (const v of violations) {
    for (const [r, c] of v.cells) set.add(`${r},${c}`);
  }
  return set;
}
