/**
 * 通达信序列函数原语库。
 *
 * 序列统一为 `(number | null)[]`，与 K 线数组等长、逐根对齐。口径约定（改动即改公式口径）:
 *   - 窗口函数在窗口不足或窗口内含 null 时返回 null（fail-closed，不伪造数值）;
 *   - 递归函数（ema/sma）遇 null 输出 null，但保留递推状态，暖期后不重置;
 *   - 算术运算空值穿透，除零返回 null;
 *   - 比较/布尔运算把 null 视为假并返回 0/1 —— 与通达信「条件不成立即信号不触发」一致;
 *   - 非有限值（NaN/Infinity）一律按 null 处理。
 */

export type Series = (number | null)[]
/** 函数参数：序列，或逐根生效的常数（通达信用法，如 CROSS(AA,20)） */
export type SeriesInput = Series | number

function valueAt(x: SeriesInput, i: number): number | null {
  if (typeof x === 'number') return Number.isFinite(x) ? x : null
  const v = x[i]
  return v == null || !Number.isFinite(v) ? null : v
}

/** 窗口长度：取整后必须 ≥ 1，否则视为不可用 */
function windowAt(n: SeriesInput, i: number): number | null {
  const raw = valueAt(n, i)
  if (raw == null) return null
  const size = Math.round(raw)
  return size >= 1 ? size : null
}

/** 结果长度：两侧同长时按序列长度，全为标量时为 0（无根可算） */
function resultLength(a: SeriesInput, b: SeriesInput): number {
  if (typeof a === 'number') return typeof b === 'number' ? 0 : b.length
  return a.length
}

function lift2(
  f: (x: number, y: number) => number | null,
  nullValue: number | null,
): (a: SeriesInput, b: SeriesInput) => Series {
  return (a, b) => {
    const out: Series = []
    for (let i = 0, len = resultLength(a, b); i < len; i++) {
      const x = valueAt(a, i)
      const y = valueAt(b, i)
      out.push(x == null || y == null ? nullValue : f(x, y))
    }
    return out
  }
}

function lift1(f: (x: number) => number): (a: SeriesInput) => Series {
  return (a) => {
    const out: Series = []
    for (let i = 0, len = resultLength(a, 0); i < len; i++) {
      const x = valueAt(a, i)
      out.push(x == null ? null : f(x))
    }
    return out
  }
}

/** REF(X,N): 取 N 根之前的值；N 可为序列（变长周期）。越界为 null。 */
export function ref(x: Series, n: SeriesInput): Series {
  return x.map((_, i) => {
    const raw = valueAt(n, i)
    if (raw == null) return null
    const offset = Math.round(raw)
    if (offset < 0) return null
    const j = i - offset
    return j < 0 ? null : valueAt(x, j)
  })
}

/** MA(X,N): 简单移动平均；窗口不足或含 null 为 null。 */
export function ma(x: Series, n: number): Series {
  return x.map((_, i) => {
    if (i < n - 1) return null
    let acc = 0
    for (let j = i - n + 1; j <= i; j++) {
      const v = valueAt(x, j)
      if (v == null) return null
      acc += v
    }
    return acc / n
  })
}

/** EMA(X,N): α=2/(N+1)；首值取首个非空输入，之后递推。 */
export function ema(x: Series, n: number): Series {
  return recurse(x, 2 / (n + 1))
}

/** SMA(X,N,M): 通达信递归加权 Y=(M*X+(N-M)*Y')/N；首值取首个非空输入。 */
export function sma(x: Series, n: number, m: number): Series {
  return recurse(x, m / n)
}

function recurse(x: Series, alpha: number): Series {
  const out: Series = new Array<number | null>(x.length).fill(null)
  let prev: number | null = null
  for (let i = 0; i < x.length; i++) {
    const v = valueAt(x, i)
    if (v == null) continue // 输出留空但保留状态: 暖期空值不得重置递推
    prev = prev == null ? v : alpha * v + (1 - alpha) * prev
    out[i] = prev
  }
  return out
}

/** HHV(X,N): N 周期内最高值。 */
export function hhv(x: Series, n: number): Series {
  return extremes(x, n, (v, best) => v > best)
}

/** LLV(X,N): N 周期内最低值。 */
export function llv(x: Series, n: number): Series {
  return extremes(x, n, (v, best) => v < best)
}

function extremes(x: Series, n: number, better: (v: number, best: number) => boolean): Series {
  return x.map((_, i) => {
    if (i < n - 1) return null
    let best: number | null = null
    for (let j = i - n + 1; j <= i; j++) {
      const v = valueAt(x, j)
      if (v == null) return null
      if (best == null || better(v, best)) best = v
    }
    return best
  })
}

/** HHVBARS(X,N): N 周期内最高值距当前的根数；并列取最早出现的极值。 */
export function hhvBars(x: Series, n: number): Series {
  return extremeBars(x, n, (v, best) => v > best)
}

/** LLVBARS(X,N): N 周期内最低值距当前的根数；并列取最早出现的极值。 */
export function llvBars(x: Series, n: number): Series {
  return extremeBars(x, n, (v, best) => v < best)
}

function extremeBars(x: Series, n: number, better: (v: number, best: number) => boolean): Series {
  return x.map((_, i) => {
    if (i < n - 1) return null
    let best: number | null = null
    let bestIdx = -1
    for (let j = i - n + 1; j <= i; j++) {
      const v = valueAt(x, j)
      if (v == null) return null
      if (best == null || better(v, best)) {
        best = v
        bestIdx = j
      }
    }
    return i - bestIdx
  })
}

/** SUM(X,N): N 周期求和；N 可为序列（变长周期）。 */
export function sum(x: Series, n: SeriesInput): Series {
  return x.map((_, i) => {
    const size = windowAt(n, i)
    if (size == null || i - size + 1 < 0) return null
    let acc = 0
    for (let j = i - size + 1; j <= i; j++) {
      const v = valueAt(x, j)
      if (v == null) return null
      acc += v
    }
    return acc
  })
}

/** SUMBARS(X,A): 自当前根向前累计 X 首次达到 A 所需的根数；到数据头仍不达时返回已用根数。 */
export function sumBars(x: Series, target: SeriesInput): Series {
  return x.map((_, i) => {
    const goal = valueAt(target, i)
    if (goal == null) return null
    let acc = 0
    let used = 0
    for (let j = i; j >= 0; j--) {
      acc += valueAt(x, j) ?? 0 // 缺失根不贡献累计
      used += 1
      if (acc >= goal) return used
    }
    return used
  })
}

/** COUNT(COND,N): N 周期内条件成立的根数。 */
export function count(cond: Series, n: number): Series {
  return cond.map((_, i) => {
    if (i < n - 1) return null
    let hits = 0
    for (let j = i - n + 1; j <= i; j++) {
      if ((cond[j] ?? 0) > 0) hits += 1
    }
    return hits
  })
}

/** CROSS(A,B): 当根 A>B 且前一根 A<=B 返回 1。B 可为常数；任一侧不可知返回 0。 */
export function cross(a: Series, b: SeriesInput): Series {
  return a.map((_, i) => {
    if (i < 1) return 0
    const a0 = valueAt(a, i)
    const b0 = valueAt(b, i)
    const a1 = valueAt(a, i - 1)
    const b1 = valueAt(b, i - 1)
    if (a0 == null || b0 == null || a1 == null || b1 == null) return 0
    return a0 > b0 && a1 <= b1 ? 1 : 0
  })
}

/** IF(COND,A,B): 条件成立取 A，否则取 B（条件不可知视为不成立）。 */
export function ifSeries(cond: Series, a: SeriesInput, b: SeriesInput): Series {
  return cond.map((_, i) => (valueAt(cond, i) ?? 0) > 0 ? valueAt(a, i) : valueAt(b, i))
}

// ===== 算术（空值穿透） =====
export const add = lift2((x, y) => x + y, null)
export const sub = lift2((x, y) => x - y, null)
export const mul = lift2((x, y) => x * y, null)
export const div = lift2((x, y) => (y === 0 ? null : x / y), null)
export const abs = lift1(Math.abs)

// ===== 比较与布尔（null 视为假，返回 0/1） =====
export const gt = lift2((x, y) => (x > y ? 1 : 0), 0)
export const ge = lift2((x, y) => (x >= y ? 1 : 0), 0)
export const lt = lift2((x, y) => (x < y ? 1 : 0), 0)
export const le = lift2((x, y) => (x <= y ? 1 : 0), 0)
export const eq = lift2((x, y) => (x === y ? 1 : 0), 0)
export const and = lift2((x, y) => (x > 0 && y > 0 ? 1 : 0), 0)
export const or = lift2((x, y) => (x > 0 || y > 0 ? 1 : 0), 0)
export const not = lift1(x => (x > 0 ? 0 : 1))
