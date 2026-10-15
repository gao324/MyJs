// 通达信序列函数原语库的行为锁定测试。
//
// 关键口径（与设计文档 §3.3 一致，改动这些断言等于改动公式口径）:
//   - SMA 是通达信递归加权 Y=(M*X+(N-M)*Y')/N，不是简单移动平均;
//   - 递归函数遇到 null 时输出 null 但保留递推状态（暖期后不重置）;
//   - 窗口函数暖期（窗口不足）返回 null，窗口内含 null 也返回 null;
//   - 算术运算空值穿透（任一操作数 null → null），除零返回 null（fail-closed）;
//   - 比较/布尔运算把 null 视为假（信号不触发），返回 0/1。
import { describe, expect, it } from 'vitest'
import {
  abs,
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
  le,
  llv,
  llvBars,
  lt,
  ma,
  mul,
  not,
  or,
  ref,
  sma,
  sub,
  sum,
  sumBars,
} from './primitives'

describe('ref 引用', () => {
  it('定长周期: 前 N 根越界为 null', () => {
    expect(ref([1, 2, 3], 1)).toEqual([null, 1, 2])
  })

  it('变长周期: 逐根取该根自己的偏移量', () => {
    expect(ref([10, 20, 30, 40], [0, 1, 2, 1])).toEqual([10, 10, 10, 30])
  })

  it('偏移越界返回 null', () => {
    expect(ref([1, 2], 3)).toEqual([null, null])
  })
})

describe('ma 简单移动平均', () => {
  it('窗口不足为 null', () => {
    expect(ma([1, 2, 3, 4], 2)).toEqual([null, 1.5, 2.5, 3.5])
  })
})

describe('ema 指数加权 (α=2/(N+1))', () => {
  it('从首值起算并递推', () => {
    const out = ema([1, 2, 3], 2) // α=2/3
    expect(out[0]).toBeCloseTo(1, 6)
    expect(out[1]).toBeCloseTo(2 / 3 * 2 + 1 / 3 * 1, 6)
    expect(out[2]).toBeCloseTo(2 / 3 * 3 + 1 / 3 * (2 / 3 * 2 + 1 / 3 * 1), 6)
  })

  it('null 输入输出 null, 但保留递推状态不重置', () => {
    const out = ema([1, null, 3], 2)
    expect(out[0]).toBeCloseTo(1, 6)
    expect(out[1]).toBeNull()
    // 第 3 根从第 1 根的状态继续 (1), 而不是把 null 当 0 或以 3 重新起算
    expect(out[2]).toBeCloseTo(2 / 3 * 3 + 1 / 3 * 1, 6)
  })
})

describe('sma 通达信递归加权', () => {
  it('Y=(M*X+(N-M)*Y\')/N, 首值等于首个 X', () => {
    const out = sma([1, 2, 3], 3, 1)
    expect(out[0]).toBeCloseTo(1, 6)
    expect(out[1]).toBeCloseTo(1 / 3 * 2 + 2 / 3 * 1, 6)
    expect(out[2]).toBeCloseTo(1 / 3 * 3 + 2 / 3 * (1 / 3 * 2 + 2 / 3 * 1), 6)
  })

  it('与简单移动平均不同 (回归点: 不可用 ma 代替)', () => {
    const x = [1, 2, 3, 4]
    expect(sma(x, 3, 1)[3]).not.toBeCloseTo(ma(x, 3)[3] as number, 4)
  })
})

describe('hhv / llv 窗口极值', () => {
  it('暖期 null, 窗口内取值', () => {
    expect(hhv([1, 5, 2, 4], 2)).toEqual([null, 5, 5, 4])
    expect(llv([1, 5, 2, 4], 2)).toEqual([null, 1, 2, 2])
  })

  it('窗口内含 null 时整窗不可用 (fail-closed)', () => {
    expect(hhv([1, null, 3], 2)).toEqual([null, null, null])
  })
})

describe('hhvBars / llvBars 极值根数', () => {
  it('并列取最早出现的极值 (自旧向新严格比较)', () => {
    // i=2 窗口 [3,1,3]: 最大值 3 最早在 idx0 → 距当前 2 根
    // i=3 窗口 [1,3,2]: 最大值 3 在 idx2 → 距当前 1 根
    expect(hhvBars([3, 1, 3, 2], 3)).toEqual([null, null, 2, 1])
    // i=3 窗口 [1,3,0]: 最小值 0 就是当前根 → 0 根
    expect(llvBars([3, 1, 3, 0], 3)).toEqual([null, null, 1, 0])
  })
})

describe('sum 求和 (定长/变长周期)', () => {
  it('定长周期暖期为 null', () => {
    expect(sum([1, 2, 3, 4], 2)).toEqual([null, 3, 5, 7])
  })

  it('变长周期逐根取该根的窗口长度', () => {
    expect(sum([1, 2, 3, 4], [1, 2, 2, 3])).toEqual([1, 3, 5, 9])
  })

  it('周期非法 (null/0) 返回 null', () => {
    expect(sum([1, 2, 3], [null, 0, 2])).toEqual([null, null, 5])
  })
})

describe('sumBars 累计达标的根数', () => {
  it('达标返回已用根数, 到数据头仍不达返回已用根数', () => {
    expect(sumBars([1, 1, 1, 1], 2)).toEqual([1, 2, 2, 2])
    expect(sumBars([1, 1], 5)).toEqual([1, 2])
  })
})

describe('count 条件计数', () => {
  it('窗口不足为 null, 否则统计 cond>0 的根数', () => {
    expect(count([1, 0, 1, 1, 0], 3)).toEqual([null, null, 2, 2, 2])
  })
})

describe('cross 上穿', () => {
  it('前一根 <= 且当根 > 才算上穿', () => {
    expect(cross([1, 2, 3, 3], [2, 2, 2, 3])).toEqual([0, 0, 1, 0])
  })

  it('第二参数可为常数', () => {
    expect(cross([1, 3], 2)).toEqual([0, 1])
  })

  it('含 null 一律视为未上穿 (前值不可知时不臆断)', () => {
    expect(cross([1, null, 3], [2, 2, 2])).toEqual([0, 0, 0])
  })
})

describe('ifSeries 条件取值', () => {
  it('cond>0 取 a, 否则取 b', () => {
    expect(ifSeries([0, 1, 0], [10, 20, 30], 0)).toEqual([0, 20, 0])
  })
})

describe('算术运算空值穿透', () => {
  it('任一操作数为 null → null', () => {
    expect(add([1, null, 3], 1)).toEqual([2, null, 4])
    expect(sub(1, [1, null, 3])).toEqual([0, null, -2])
    expect(mul([2, null], [3, 3])).toEqual([6, null])
  })

  it('除零返回 null (fail-closed, 不伪造数值)', () => {
    expect(div([1, 2], [0, 2])).toEqual([null, 1])
  })

  it('abs 取绝对值', () => {
    expect(abs([-1, 2, null])).toEqual([1, 2, null])
  })
})

describe('比较与布尔运算把 null 视为假', () => {
  it('比较返回 0/1', () => {
    expect(gt([1, null], 0)).toEqual([1, 0])
    expect(le([null, 2], 1)).toEqual([0, 0])
    expect(eq([0, 1, 2], 1)).toEqual([0, 1, 0])
    expect(lt([0, 5], 3)).toEqual([1, 0])
  })

  it('and / or / not 按 0/1 语义', () => {
    expect(and([1, 0], [1, 1])).toEqual([1, 0])
    expect(or([1, 0], [0, 0])).toEqual([1, 0])
    expect(not([1, 0])).toEqual([0, 1])
  })
})
