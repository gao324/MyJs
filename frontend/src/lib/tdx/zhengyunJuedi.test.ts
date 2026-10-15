// 「郑云绝底」公式翻译层的行为锁定测试。
//
// 测试策略: 公式模块的输出与「用原语库独立重算一遍」逐根对齐 —— 信号与 DRAWICON
// 落点不是照抄实现，而是按原始公式条件重新推导后比对，避免测试与实现同错。
import { describe, expect, it } from 'vitest'
import { and, cross, div, gt, hhvBars, lt, ma, mul, or, ref, sub, type Series } from './primitives'
import { computeZhengyunJuedi } from './zhengyunJuedi'
import { syntheticBars } from './barsFixture'

/** 取序列中非空值的集合 (断言取值域用) */
function distinct(s: Series): number[] {
  return [...new Set(s.filter((v): v is number => v != null))]
}

/** 条件为真的索引 (与实现同口径: 比较/布尔把 null 视为假) */
function idxWhere(cond: Series): number[] {
  const out: number[] = []
  cond.forEach((v, i) => { if (v != null && v > 0) out.push(i) })
  return out
}

const bars = syntheticBars()
const r = computeZhengyunJuedi(bars)
const close: Series = bars.map(b => b.close)
const high: Series = bars.map(b => b.high)
const open: Series = bars.map(b => b.open)

describe('郑云绝底 输出结构', () => {
  it('所有输出与输入等长', () => {
    for (const key of ['aa', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a', 'var17', 'var18',
      'guanzhu', 'jipindi', 'mai', 'maiY', 'juedi1', 'juedi2'] as const) {
      expect(r[key], key).toHaveLength(bars.length)
    }
    expect(r.refLines).toEqual([10, 80])
  })

  it('暖期按窗口长度返回 null, 之后有值', () => {
    // AA ← Q2 需要 18 根 (LLV/HHV(LOW/HIGH,18))
    expect(r.aa[16]).toBeNull()
    expect(r.aa[17]).not.toBeNull()
    // VAR17 ← HHV/LLV(HIGH/LOW,34)
    expect(r.var17[32]).toBeNull()
    expect(r.var17[33]).not.toBeNull()
  })
})

describe('郑云绝底 信号取值域', () => {
  it('卖 ∈ {85,90}, 关注 ∈ {0,10}, 极品底 ∈ {0,5}, 买 ∈ {0,10}', () => {
    expect(distinct(r.maiY).every(v => v === 85 || v === 90)).toBe(true)
    expect(distinct(r.guanzhu).every(v => v === 0 || v === 10)).toBe(true)
    expect(distinct(r.jipindi).every(v => v === 0 || v === 5)).toBe(true)
    expect(distinct(r.mai).every(v => v === 0 || v === 10)).toBe(true)
  })

  it('合成数据确实触发过信号 (防止断言空转)', () => {
    const fired = [r.guanzhu, r.jipindi, r.mai].some(s => s.some(v => v != null && v > 0))
      || r.maiY.some(v => v === 85)
    expect(fired).toBe(true)
  })

  it('郑云绝底1 ∈ {0,-5}, 郑云绝底2 ∈ {0,-3}, 且 2 非零时 1 必非零 (绝底=1 ⇒ 绝底>0)', () => {
    expect(distinct(r.juedi1).every(v => v === 0 || v === -5)).toBe(true)
    expect(distinct(r.juedi2).every(v => v === 0 || v === -3)).toBe(true)
    r.juedi2.forEach((v, i) => {
      if (v === -3) expect(r.juedi1[i]).toBe(-5)
    })
  })
})

describe('郑云绝底 信号条件与原公式逐根一致', () => {
  it('买 = CROSS(AA,VAR18) AND 18>REF(AA,1) (字面语义: 昨日 AA 小于 18)', () => {
    const cond = and(cross(r.aa, r.var18), gt(18, ref(r.aa, 1)))
    expect(idxWhere(cond).every(i => r.mai[i] === 10)).toBe(true)
    r.mai.forEach((v, i) => {
      if (v === 10) expect(cond[i]).toBe(1)
    })
  })

  it('卖 = CROSS(VAR17,AA) AND REF(AA,1)>80 → 85, 否则 90', () => {
    const cond = and(cross(r.var17, r.aa), gt(ref(r.aa, 1), 80))
    r.maiY.forEach((v, i) => {
      expect(v).toBe(cond[i] === 1 ? 85 : 90)
    })
  })

  it('关注 = CROSS(AA,20) AND Q10<-13', () => {
    const q8 = hhvBars(high, 50)
    const q10 = mul(div(sub(close, ref(open, q8)), ref(open, q8)), 100)
    const cond = and(cross(r.aa, 20), lt(q10, -13))
    r.guanzhu.forEach((v, i) => {
      expect(v).toBe(cond[i] === 1 ? 10 : 0)
    })
  })

  it('极品底 = (MA(C,5)-C)/C>0.04 AND (MA(C,10)-MA(C,5))/MA(C,5)>0.04', () => {
    const shortMa = ma(close, 5)
    const midMa = ma(close, 10)
    const cond = and(
      gt(div(sub(shortMa, close), close), 0.04),
      gt(div(sub(midMa, shortMa), shortMa), 0.04),
    )
    r.jipindi.forEach((v, i) => {
      expect(v).toBe(cond[i] === 1 ? 5 : 0)
    })
  })
})

describe('郑云绝底 DRAWICON 落点', () => {
  it('图标仅出现在条件满足且落点可算出的根上, 坐标与公式一致', () => {
    const q1 = mul(div(sub(close, ref(close, 1)), ref(close, 1)), 100)
    const q5 = gt(r.aa, ref(r.aa, 1))
    const cond1 = and(cross(r.aa, r.a5), lt(ref(r.aa, 1), 34))
    const cond2 = and(cross(r.a1, r.aa), gt(ref(r.aa, 1), 80))
    const cond3 = or(
      and(and(and(cross(r.aa, r.a1), q5), lt(r.a1, 20)), gt(q1, 9)),
      and(and(gt(r.a1, ref(r.a1, 1)), gt(q1, 4)), lt(r.a1, 60)),
    )

    const expected = (cond: Series, y: Series, kind: 1 | 5 | 7) =>
      idxWhere(cond)
        .filter(i => y[i] != null)
        .map(i => ({ index: i, y: y[i] as number, kind }))

    expect(r.icons).toEqual([
      ...expected(cond1, ref(r.aa, 1), 1),
      ...expected(cond2, ref(r.aa, 3), 5),
      ...expected(cond3, [30], 7).map(({ index }) => ({ index, y: 30, kind: 7 as const })),
    ])
    r.icons.forEach(({ index, y }) => {
      expect(index).toBeGreaterThanOrEqual(0)
      expect(index).toBeLessThan(bars.length)
      expect(Number.isFinite(y)).toBe(true)
    })
  })

  it('合成数据确实触发了图标条件 (防止断言空转)', () => {
    expect(r.icons.length).toBeGreaterThan(0)
  })
})
