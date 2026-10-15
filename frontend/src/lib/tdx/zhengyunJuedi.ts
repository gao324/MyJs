/**
 * 通达信副图公式「郑云绝底」的翻译层。
 *
 * 逐行对应原始公式；按依赖剪枝只实现被输出引用的活跃子图 —— 原公式中
 * VAR1~VAR9（含 VAR2:=SUMBARS(VOL,CAPITAL)）、VAR10、VAR12~VAR15、Q7、Q9、Q11
 * 未被任何画线/信号/DRAWICON 引用，不实现。因此本指标不依赖流通股本，
 * ETF/指数等无股本标的同样可用。
 *
 * 口径细节见 docs/superpowers/specs/2026-10-14-tdx-juedi-subchart-design.md §3。
 */
import {
  add,
  and,
  count,
  cross,
  div,
  ema,
  eq,
  gt,
  hhv,
  hhvBars,
  ifSeries,
  llv,
  lt,
  ma,
  mul,
  or,
  ref,
  sma,
  sub,
  type Series,
} from './primitives'

/** 公式计算所需的日线输入（与图表 OHLC 结构兼容，工具层不反向依赖组件层）。 */
export interface TdxBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

/** DRAWICON 落点：index = 数据索引，y = 纵坐标，kind = 通达信图标号 */
export interface JuediIcon {
  index: number
  y: number
  kind: 1 | 5 | 7
}

export interface JuediResult {
  /** AA:3*Q3-2*Q4 */
  aa: Series
  a1: Series
  a2: Series
  a3: Series
  a4: Series
  a5: Series
  a6: Series
  /** A:EMA(A4,3) */
  a: Series
  /** STICKLINE 上下端（VAR17 快线 / VAR18 慢线） */
  var17: Series
  var18: Series
  /** 关注:IF(CROSS(AA,20) AND Q10<-13,10,0) */
  guanzhu: Series
  /** 极品底:IF(…) → 0/5 */
  jipindi: Series
  /** 买:IF(CROSS(AA,VAR18) AND 18>REF(AA,1),10,0) */
  mai: Series
  /** 卖:IF(CROSS(VAR17,AA) AND REF(AA,1)>80,85,90) */
  maiY: Series
  /** 郑云绝底:IF(绝底>0,-5,0) */
  juedi1: Series
  /** 郑云绝底2:IF(绝底=1,-3,0) */
  juedi2: Series
  icons: JuediIcon[]
  refLines: number[]
}

function collectIcons(out: JuediIcon[], cond: Series, y: Series | number, kind: JuediIcon['kind']): void {
  cond.forEach((flag, i) => {
    if (flag == null || flag <= 0) return
    const value = typeof y === 'number' ? y : y[i]
    if (value == null) return
    out.push({ index: i, y: value, kind })
  })
}

function compute(bars: readonly TdxBar[]): JuediResult {
  const close: Series = bars.map(b => b.close)
  const high: Series = bars.map(b => b.high)
  const low: Series = bars.map(b => b.low)
  const open: Series = bars.map(b => b.open)
  const vol: Series = bars.map(b => b.volume ?? 0)

  // —— Q1/Q2 与 AA 线 ——
  const q1 = mul(div(sub(close, ref(close, 1)), ref(close, 1)), 100)
  const q2 = mul(div(sub(close, llv(low, 18)), sub(hhv(high, 18), llv(low, 18))), 100)
  const q3 = sma(q2, 9, 1)
  const q4 = sma(q3, 3, 1)
  const aa = sub(mul(q3, 3), mul(q4, 2))
  const q5 = gt(aa, ref(aa, 1))
  const q8 = hhvBars(high, 50)
  const q10 = mul(div(sub(close, ref(open, q8)), ref(open, q8)), 100)

  // —— A1~A6 与 A 线 ——
  const a1 = ema(q3, 3)
  const a2 = ema(a1, 3)
  const a3 = ema(a2, 3)
  const a4 = ema(a3, 3)
  const a5 = ema(a4, 3)
  const a6 = ema(a5, 3)
  const a = ema(a4, 3)

  // —— VAR17/VAR18 趋势线（STICKLINE 与买/卖信号来源） ——
  const var11 = div(add(add(mul(close, 2), high), low), 4)
  const var13 = llv(low, 34)
  const var16 = hhv(high, 34)
  const var17 = ema(mul(div(sub(var11, var13), sub(var16, var13)), 100), 8)
  const var18 = ema(var17, 5)

  // —— 信号 ——
  const guanzhu = ifSeries(and(cross(aa, 20), lt(q10, -13)), 10, 0)
  const jipindi = ifSeries(
    and(
      gt(div(sub(ma(close, 5), close), close), 0.04),
      gt(div(sub(ma(close, 10), ma(close, 5)), ma(close, 5)), 0.04),
    ),
    5,
    0,
  )
  // 原文 "18>REF(AA,1)" 按字面执行（常数 18 大于昨日 AA），不做"作者本意"修正
  const mai = ifSeries(and(cross(aa, var18), gt(18, ref(aa, 1))), 10, 0)
  const maiY = ifSeries(and(cross(var17, aa), gt(ref(aa, 1), 80)), 85, 90)

  // —— 绝底（AND 优先级高于 OR，按字面展开） ——
  const bias = (n: number): Series => mul(div(sub(close, ma(close, n)), ma(close, n)), 100)
  const v1 = lt(mul(div(close, ma(close, 40)), 100), 78)
  const v2 = lt(mul(div(close, ma(close, 60)), 100), 74)
  const v3 = gt(high, mul(low, 1.03))
  const v4 = and(v3, gt(count(v3, 5), 1))
  const juediCond = or(
    or(
      and(
        and(
          and(and(v4, or(v1, v2)), lt(close, div(ma(close, 10), 1.1))),
          lt(div(vol, ref(vol, 1)), 1),
        ),
        lt(bias(6), -20),
      ),
      lt(bias(12), -20),
    ),
    lt(bias(24), -20),
  )
  const juedi1 = ifSeries(gt(juediCond, 0), -5, 0)
  const juedi2 = ifSeries(eq(juediCond, 1), -3, 0)

  // —— DRAWICON ×3（图标号与落点见设计文档 §4.4） ——
  const icons: JuediIcon[] = []
  collectIcons(icons, and(cross(aa, a5), lt(ref(aa, 1), 34)), ref(aa, 1), 1)
  collectIcons(icons, and(cross(a1, aa), gt(ref(aa, 1), 80)), ref(aa, 3), 5)
  collectIcons(
    icons,
    or(
      and(and(and(cross(aa, a1), q5), lt(a1, 20)), gt(q1, 9)),
      and(and(gt(a1, ref(a1, 1)), gt(q1, 4)), lt(a1, 60)),
    ),
    30,
    7,
  )

  return {
    aa, a1, a2, a3, a4, a5, a6, a, var17, var18,
    guanzhu, jipindi, mai, maiY, juedi1, juedi2,
    icons,
    refLines: [10, 80],
  }
}

/** 计算缓存: key 为 K 线数组引用 —— dataZoom/悬停引发的 option 重建零重算，换数据自动失效 */
const cache = new WeakMap<readonly TdxBar[], JuediResult>()

export function computeZhengyunJuedi(bars: readonly TdxBar[]): JuediResult {
  const hit = cache.get(bars)
  if (hit) return hit
  const result = compute(bars)
  cache.set(bars, result)
  return result
}
