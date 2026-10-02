import { useEffect, useMemo, useState } from "react";
import { fetchState, postConfirm, postPreview } from "./api";
import { findViolations, violationCellSet } from "./weave";
import "./styles.css";

const MAX_ROWS = 16;
const MAX_COLS = 8;

function cloneDesign(d) {
  return {
    rows: d.rows,
    cols: d.cols,
    k: d.k,
    grid: d.grid.map((row) => [...row]),
    locks: d.locks.map((p) => [...p]),
    onesRange: Array.isArray(d.onesRange) ? [...d.onesRange] : null,
  };
}

function sortLocks(locks) {
  return [...locks].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
}

function countOnes(grid) {
  return grid.reduce((n, row) => n + row.reduce((m, v) => m + v, 0), 0);
}

/** 区间是否一致（同缺省或两端相同）。 */
function sameRange(a, b) {
  if (a == null && b == null) return true;
  if (a == null || b == null) return false;
  return a[0] === b[0] && a[1] === b[1];
}

function sameDesign(a, b) {
  if (!a || !b) return false;
  if (a.rows !== b.rows || a.cols !== b.cols || a.k !== b.k) return false;
  if (!sameRange(a.onesRange, b.onesRange)) return false;
  for (let r = 0; r < a.rows; r += 1) {
    for (let c = 0; c < a.cols; c += 1) {
      if (a.grid[r][c] !== b.grid[r][c]) return false;
    }
  }
  const la = sortLocks(a.locks);
  const lb = sortLocks(b.locks);
  if (la.length !== lb.length) return false;
  return la.every((p, i) => p[0] === lb[i][0] && p[1] === lb[i][1]);
}

function validateDraft(d) {
  if (!Number.isInteger(d.rows) || d.rows < 1 || d.rows > MAX_ROWS) {
    return `行数必须是 1~${MAX_ROWS} 的整数`;
  }
  if (!Number.isInteger(d.cols) || d.cols < 1 || d.cols > MAX_COLS) {
    return `列数必须是 1~${MAX_COLS} 的整数`;
  }
  if (!Number.isInteger(d.k) || d.k < 1) return "最大浮长 K 必须是正整数";
  if (d.onesRange != null) {
    const [lo, hi] = d.onesRange;
    const total = d.rows * d.cols;
    if (!Number.isInteger(lo) || !Number.isInteger(hi) || lo < 0 || hi < 0) {
      return "格值1总数区间两端都必须是非负整数（或两栏留空表示不限制）";
    }
    if (lo > hi) return "格值1总数区间下限不得大于上限";
    if (hi > total) {
      return `格值1总数区间上限 ${hi} 超出当前尺寸总格数 ${total}（${d.rows}×${d.cols}），请调整区间或尺寸，不会自动截断`;
    }
  }
  return null;
}

function lockSetOf(locks) {
  return new Set(locks.map(([r, c]) => `${r},${c}`));
}

/** 数字输入：失焦/回车时提交，非法输入还原并提示（拒绝非法编辑）。 */
function NumberField({ label, value, min, max, onCommit, onInvalid }) {
  const [text, setText] = useState(String(value));
  useEffect(() => { setText(String(value)); }, [value]);

  function commit() {
    const n = Number(text);
    if (Number.isInteger(n) && n >= min && n <= max) {
      if (n !== value) onCommit(n);
    } else {
      setText(String(value));
      onInvalid();
    }
  }

  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        value={text}
        min={min}
        max={max}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
      />
    </label>
  );
}

/**
 * 格值 1 总数区间 [下限, 上限]。两栏皆空表示不填（null，沿用现有修补规则）；
 * 任一栏有值即视为填写，两端都必须是 0..总格数 内的整数且下限<=上限。
 * 越界只提示、不截断，由调用方在尺寸变化后给出错误横幅。
 */
function RangeField({ value, total, onChange, onInvalid }) {
  const [loText, setLoText] = useState(value ? String(value[0]) : "");
  const [hiText, setHiText] = useState(value ? String(value[1]) : "");
  useEffect(() => {
    setLoText(value ? String(value[0]) : "");
    setHiText(value ? String(value[1]) : "");
  }, [value]);

  function commit() {
    if (loText.trim() === "" && hiText.trim() === "") {
      onChange(null);
      return;
    }
    const lo = Number(loText);
    const hi = Number(hiText);
    if (!Number.isInteger(lo) || !Number.isInteger(hi) || lo < 0 || hi < 0
        || lo > hi || hi > total) {
      // 还原为当前生效值并提示，不悄悄截断
      setLoText(value ? String(value[0]) : "");
      setHiText(value ? String(value[1]) : "");
      onInvalid();
      return;
    }
    onChange([lo, hi]);
  }

  return (
    <label className="field range-field">
      <span>格值1总数区间</span>
      <input
        type="number"
        value={loText}
        min={0}
        max={total}
        placeholder="下限"
        aria-label="格值1总数下限（留空表示不限制）"
        onChange={(e) => setLoText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
      />
      <span className="range-sep">~</span>
      <input
        type="number"
        value={hiText}
        min={0}
        max={total}
        placeholder="上限"
        aria-label="格值1总数上限（留空表示不限制）"
        onChange={(e) => setHiText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
      />
      <span className="range-total">总格数 {total}；两栏留空=不限制</span>
    </label>
  );
}

/** 织物组织网格。markOf(r, c, key) 返回附加样式名（violation / flipped）。 */
function WeaveGrid({ grid, lockSet, markOf, onCellClick, small }) {
  const cols = grid[0]?.length ?? 0;
  return (
    <div
      className={`weave-grid${small ? " small" : ""}${onCellClick ? "" : " readonly"}`}
      style={{ gridTemplateColumns: `repeat(${cols}, var(--cell))` }}
    >
      {grid.map((row, r) =>
        row.map((v, c) => {
          const key = `${r},${c}`;
          const locked = lockSet?.has(key);
          const mark = markOf ? markOf(r, c, key) : "";
          return (
            <button
              key={key}
              type="button"
              disabled={!onCellClick}
              className={`cell v${v}${locked ? " locked" : ""}${mark ? ` ${mark}` : ""}`}
              onClick={onCellClick ? () => onCellClick(r, c) : undefined}
              title={`第 ${r + 1} 行第 ${c + 1} 列 · ${v === 0 ? "0 经线在上" : "1 纬线在上"}${locked ? " · 已锁定" : ""}`}
            >
              <span className="val">{v}</span>
              {locked && <span className="badge">🔒</span>}
              {mark === "flipped" && <span className="badge flip">↺</span>}
            </button>
          );
        }))}
    </div>
  );
}

function ViolationList({ violations, k }) {
  if (violations.length === 0) {
    return <p className="ok-text">✓ 无超限段，当前稿件合法</p>;
  }
  return (
    <ul className="violation-list">
      {violations.map((v, i) => (
        <li key={i}>
          第 {v.index + 1} {v.kind === "row" ? "行" : "列"}：
          {v.length === null
            ? `全同为 ${v.value}（视为无限浮长）`
            : `环向连续 ${v.length} 个 ${v.value}`}
          ，超过 K={k}
        </li>
      ))}
    </ul>
  );
}

export default function App() {
  const [original, setOriginal] = useState(null);
  const [draft, setDraft] = useState(null);
  const [mode, setMode] = useState("value");
  const [preview, setPreview] = useState(null);
  // 当前查看/选中的候选方案序号（0 = 字典序最小，也是默认确认方案）
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchState()
      .then((s) => { setOriginal(s); setDraft(cloneDesign(s)); })
      .catch((e) => setMessage({ type: "error", text: `加载原稿失败：${e.message}` }));
  }, []);

  const draftViolations = useMemo(
    () => (draft ? findViolations(draft.grid, draft.rows, draft.cols, draft.k) : []),
    [draft],
  );
  const draftViolationCells = useMemo(() => violationCellSet(draftViolations), [draftViolations]);
  const draftLockSet = useMemo(() => (draft ? lockSetOf(draft.locks) : new Set()), [draft]);
  const draftOnesCount = useMemo(
    () => (draft ? countOnes(draft.grid) : 0), [draft]);
  const draftTotalCells = draft ? draft.rows * draft.cols : 0;
  const rangeOutOfBounds = Boolean(
    draft?.onesRange && draft.onesRange[1] > draftTotalCells,
  );

  const previewStale = Boolean(preview && draft && !sameDesign(draft, preview.input));
  const dirty = Boolean(original && draft && !sameDesign(draft, original));
  const canConfirm = Boolean(
    preview && preview.solvable && !previewStale && !busy && !rangeOutOfBounds);

  // 候选列表：新预览带 candidates（至多 3 个同样最优方案），
  // 旧响应/单候选时退化为仅首个方案。selected 为当前查看并将用于确认的方案。
  const candidates = useMemo(() => {
    if (!preview || !preview.solvable) return [];
    if (Array.isArray(preview.candidates) && preview.candidates.length > 0) {
      return preview.candidates;
    }
    return [{
      repaired: preview.repaired,
      flips: preview.flips,
      onesCount: preview.repairedOnesCount,
    }];
  }, [preview]);
  const safeIndex = Math.min(candidateIndex, candidates.length - 1);
  const selected = candidates[safeIndex] ?? null;

  const flipSet = useMemo(
    () => new Set((selected?.flips ?? []).map(([r, c]) => `${r},${c}`)),
    [selected],
  );
  const previewLockSet = useMemo(
    () => (preview ? lockSetOf(preview.input.locks) : new Set()),
    [preview],
  );

  function handleCellClick(r, c) {
    setDraft((d) => {
      if (mode === "value") {
        const grid = d.grid.map((row, i) =>
          (i === r ? row.map((v, j) => (j === c ? 1 - v : v)) : row));
        return { ...d, grid };
      }
      const has = d.locks.some(([ir, ic]) => ir === r && ic === c);
      const locks = has
        ? d.locks.filter(([ir, ic]) => !(ir === r && ic === c))
        : sortLocks([...d.locks, [r, c]]);
      return { ...d, locks };
    });
  }

  function handleResize(rows, cols) {
    // 尺寸变化后区间越界：保留原值并提示调整，绝不悄然截断
    if (draft.onesRange && draft.onesRange[1] > rows * cols) {
      const [lo, hi] = draft.onesRange;
      setMessage({
        type: "error",
        text: `格值1总数区间 [${lo}, ${hi}] 已超出新尺寸总格数 ${rows * cols}（${rows}×${cols}），请调整区间（原区间未被截断）`,
      });
    }
    setDraft((d) => {
      const grid = Array.from({ length: rows }, (_, r) =>
        Array.from({ length: cols }, (_, c) =>
          (r < d.rows && c < d.cols ? d.grid[r][c] : 0)));
      const locks = d.locks.filter(([r, c]) => r < rows && c < cols);
      return { ...d, rows, cols, grid, locks };
    });
  }

  function handleRangeChange(next) {
    setDraft((d) => ({ ...d, onesRange: next }));
    if (next) {
      setMessage({
        type: "info",
        text: `已设置格值1总数区间 [${next[0]}, ${next[1]}]，预览将只从总数达标的修补稿中选解。`,
      });
    } else {
      setMessage({ type: "info", text: "已清空格值1总数区间，预览沿用原有修补规则（不限制 1 的总数）。" });
    }
  }

  function fillAll(value) {
    setDraft((d) => ({
      ...d,
      grid: d.grid.map((row) => row.map(() => value)),
    }));
  }

  async function handlePreview() {
    const err = validateDraft(draft);
    if (err) {
      setMessage({ type: "error", text: err });
      return;
    }
    setBusy(true);
    try {
      const result = await postPreview(draft);
      setPreview(result);
      setCandidateIndex(0);
      const rangeText = result.onesRange
        ? `，格值1总数须在 [${result.onesRange[0]}, ${result.onesRange[1]}] 内`
        : "";
      const tieText = result.solvable && result.candidateCount > 1
        ? `，并列出前 ${result.candidateCount} 个同样最优方案可逐个比较`
        : "";
      setMessage(result.solvable
        ? { type: "success", text: `预览成功：最少翻转 ${result.flipCount} 格${rangeText}${tieText}，确认后覆盖原稿。` }
        : { type: "info", text: "无可行修补：在锁定格、浮长与格值1总数区间约束下找不到达标稿件，已保留原稿，请调整后重试。" });
    } catch (e) {
      setPreview(null);
      setCandidateIndex(0);
      setMessage({ type: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm() {
    if (!preview?.token || !selected) return;
    setBusy(true);
    try {
      const { state } = await postConfirm(preview.token, draft, safeIndex);
      setOriginal(state);
      setDraft(cloneDesign(state));
      setPreview(null);
      setCandidateIndex(0);
      setMessage({
        type: "success",
        text: candidates.length > 1
          ? `已确认第 ${safeIndex + 1} 个候选方案并覆盖原稿，后续编辑基于新原稿。`
          : "已确认当前预览并覆盖原稿，后续编辑基于新原稿。",
      });
    } catch (e) {
      if (e.status === 409) {
        // 冲突（令牌已消费/原稿已更新/草稿不匹配）：清除旧预览，
        // 重新拉取原稿，保留当前未提交草稿，提示重新预览。
        setPreview(null);
        setCandidateIndex(0);
        try {
          const s = await fetchState();
          setOriginal(s);
          setMessage({
            type: "error",
            text: `${e.message} 已刷新原稿，未提交的草稿保持不变，请重新预览后再确认。`,
          });
        } catch (refreshErr) {
          setMessage({
            type: "error",
            text: `${e.message} 刷新原稿也失败了：${refreshErr.message}，旧预览已清除，请稍后重试。`,
          });
        }
      } else {
        setMessage({ type: "error", text: e.message });
      }
    } finally {
      setBusy(false);
    }
  }

  function handleReset() {
    setDraft(cloneDesign(original));
    setMessage({ type: "info", text: "草稿已还原为当前原稿。" });
  }

  if (!original || !draft) {
    return (
      <div className="page">
        {message && <div className={`banner ${message.type}`}>{message.text}</div>}
        <div className="loading">加载中…</div>
      </div>
    );
  }

  return (
    <div className="page">
      <header>
        <h1>周期平铺织物组织设计</h1>
        <p className="subtitle">
          0 = 经线在上，1 = 纬线在上；每行/每列的相同值连续段按环向（左右、上下边界相连）计，
          长度不得超过 K，全同的一行或一列视为无限浮长。
        </p>
      </header>

      {message && <div className={`banner ${message.type}`}>{message.text}</div>}

      <section className="toolbar">
        <NumberField
          label={`行数 (1~${MAX_ROWS})`} value={draft.rows} min={1} max={MAX_ROWS}
          onCommit={(n) => handleResize(n, draft.cols)}
          onInvalid={() => setMessage({ type: "error", text: `行数必须是 1~${MAX_ROWS} 的整数，已拒绝` })}
        />
        <NumberField
          label={`列数 (1~${MAX_COLS})`} value={draft.cols} min={1} max={MAX_COLS}
          onCommit={(n) => handleResize(draft.rows, n)}
          onInvalid={() => setMessage({ type: "error", text: `列数必须是 1~${MAX_COLS} 的整数，已拒绝` })}
        />
        <NumberField
          label="最大浮长 K" value={draft.k} min={1} max={Number.MAX_SAFE_INTEGER}
          onCommit={(n) => setDraft((d) => ({ ...d, k: n }))}
          onInvalid={() => setMessage({ type: "error", text: "最大浮长 K 必须是正整数，已拒绝" })}
        />

        <RangeField
          value={draft.onesRange}
          total={draftTotalCells}
          onChange={handleRangeChange}
          onInvalid={() => setMessage({
            type: "error",
            text: `格值1总数区间须两端同时填写非负整数，且 0 ≤ 下限 ≤ 上限 ≤ ${draftTotalCells}；两栏都留空才表示不限制（已还原，未截断）`,
          })}
        />

        <div className="mode-switch" role="group" aria-label="编辑模式">
          <button
            type="button"
            className={mode === "value" ? "active" : ""}
            onClick={() => setMode("value")}
          >
            编辑格值
          </button>
          <button
            type="button"
            className={mode === "lock" ? "active" : ""}
            onClick={() => setMode("lock")}
          >
            编辑锁定
          </button>
        </div>

        <div className="actions">
          <button type="button" className="primary" disabled={busy} onClick={handlePreview}>
            预览修补
          </button>
          <button type="button" className="confirm" disabled={!canConfirm} onClick={handleConfirm}>
            {preview?.solvable && candidates.length > 1
              ? `确认应用方案 ${safeIndex + 1}`
              : "确认应用预览"}
          </button>
          <button type="button" disabled={busy || !dirty} onClick={handleReset}>
            还原为原稿
          </button>
        </div>

        <div className="quick">
          <button type="button" onClick={() => fillAll(0)}>全部置 0</button>
          <button type="button" onClick={() => fillAll(1)}>全部置 1</button>
          <button type="button" onClick={() => setDraft((d) => ({ ...d, locks: [] }))}>
            清空锁定
          </button>
        </div>

        <span className={`dirty-flag ${dirty ? "yes" : ""}`}>
          {dirty ? "● 草稿有未确认修改" : "草稿与原稿一致"}
        </span>
      </section>

      <main>
        <section className="panel">
          <h2>草稿（点击格子{mode === "value" ? "翻转 0/1" : "加/解锁"}）</h2>
          <WeaveGrid
            grid={draft.grid}
            lockSet={draftLockSet}
            onCellClick={handleCellClick}
            markOf={(r, c, key) => (draftViolationCells.has(key) ? "violation" : "")}
          />
          <p className={`meta count-line${rangeOutOfBounds ? " out-of-range" : ""}`}>
            格值1总数：<strong>{draftOnesCount}</strong> / {draftTotalCells}
            {draft.onesRange
              ? <> · 要求区间 <strong>[{draft.onesRange[0]}, {draft.onesRange[1]}]</strong>
                  {rangeOutOfBounds
                    ? " · ⚠ 区间已越界，请调整后再预览"
                    : (draftOnesCount >= draft.onesRange[0] && draftOnesCount <= draft.onesRange[1]
                        ? " · 当前草稿总数已在区间内"
                        : " · 当前草稿总数不在区间内（以修补稿达标为准）")}
                </>
              : " · 未设置格值1总数区间"}
          </p>
          <h3>超限段（{draftViolations.length}）</h3>
          <ViolationList violations={draftViolations} k={draft.k} />
        </section>

        <aside>
          <section className="panel">
            <h2>当前原稿 <span className="meta">v{original.version}</span></h2>
            <WeaveGrid grid={original.grid} lockSet={lockSetOf(original.locks)} small />
            <p className="meta">
              {original.rows} 行 × {original.cols} 列 · K={original.k} ·
              锁定 {original.locks.length} 格 · 超限段 {original.violations.length} 个
            </p>
            <p className="meta">
              格值1总数：<strong>{original.onesCount ?? countOnes(original.grid)}</strong>
              / {original.rows * original.cols}
              {Array.isArray(original.onesRange)
                ? <> · 区间 <strong>[{original.onesRange[0]}, {original.onesRange[1]}]</strong></>
                : " · 区间：未设置"}
            </p>
            <ViolationList violations={original.violations} k={original.k} />
          </section>

          {preview && (
            <section className={`panel preview ${previewStale ? "stale" : ""}`}>
              <h2>修补预览 {previewStale && <span className="stale-tag">已失效</span>}</h2>
              {previewStale && (
                <p className="warn-text">
                  预览后草稿（交织格、锁定格、K 或格值1总数区间）已变动，旧预览失效，请重新预览。
                </p>
              )}
              {preview.solvable && selected ? (
                <>
                  {candidates.length > 1 && (
                    <div className="candidate-bar" role="group" aria-label="并列最优候选方案">
                      {candidates.map((cand, i) => (
                        <button
                          key={i}
                          type="button"
                          className={i === safeIndex ? "active" : ""}
                          aria-pressed={i === safeIndex}
                          onClick={() => setCandidateIndex(i)}
                        >
                          方案 {i + 1}
                        </button>
                      ))}
                      <span className="meta">
                        {candidates.length} 个同样最优方案（各翻转 {preview.flipCount} 格），按行展开字典序
                      </span>
                    </div>
                  )}
                  <WeaveGrid
                    grid={selected.repaired}
                    lockSet={previewLockSet}
                    markOf={(r, c, key) => (flipSet.has(key) ? "flipped" : "")}
                  />
                  <p className="meta count-line">
                    格值1总数：{preview.onesCount} → <strong>{selected.onesCount}</strong>
                    {preview.onesRange
                      ? <> · 要求区间 <strong>[{preview.onesRange[0]}, {preview.onesRange[1]}]</strong>
                          {selected.onesCount >= preview.onesRange[0]
                            && selected.onesCount <= preview.onesRange[1]
                            ? " ✓ 达标" : " ✗ 未达标"}
                        </>
                      : " · 未设置区间（沿用原修补规则）"}
                  </p>
                  <p className="meta">
                    方案 {safeIndex + 1}/{candidates.length} · 最少翻转 {selected.flips.length} 格
                    {selected.flips.length > 0 && "（绿框 ↺ 为翻转位置）"}
                    {candidates.length > 1 && " · 确认应用当前查看的方案"}
                  </p>
                  {selected.flips.length > 0 && (
                    <ul className="flip-list">
                      {selected.flips.map(([r, c]) => (
                        <li key={`${r},${c}`}>
                          第 {r + 1} 行第 {c + 1} 列：
                          {preview.input.grid[r][c]} → {selected.repaired[r][c]}
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : preview.solvable ? null : (
                <p className="warn-text">
                  无可行修补：在锁定格、环向浮长 K
                  {preview.onesRange
                    ? ` 与格值1总数区间 [${preview.onesRange[0]}, ${preview.onesRange[1]}]`
                    : ""}
                  的约束下不存在达标稿件，原稿保持不变。
                </p>
              )}
            </section>
          )}
        </aside>
      </main>

      <footer>
        <span className="legend-cell v0">0</span> 经线在上
        <span className="legend-cell v1">1</span> 纬线在上
        <span className="legend-cell locked">🔒</span> 锁定格（修补时不可翻转）
        <span className="legend-cell violation">0</span> 超限段
        <span className="legend-cell flipped">↺</span> 翻转位置
      </footer>
    </div>
  );
}
