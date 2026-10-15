// 「郑云绝底」副图 series 映射层的契约测试。
//
// 映射层只产出 ECharts option 对象、不依赖 echarts 运行时, 因此可在纯 node 环境断言:
//   - 每个输出都有对应 series, 类型/颜色与设计文档 §4.4 一致;
//   - STICKLINE 柱按 VAR17-VAR18 符号染红/绿, 相等不画;
//   - 信号柱 0 值不画 (null), DRAWICON 按图标号拆成三个 scatter;
//   - 悬停信息栏按数据索引给出当前值。
import { describe, expect, it } from 'vitest'
import { juediSubInfo, juediSubSeries } from './juediChart'
import { computeZhengyunJuedi, type TdxBar } from './zhengyunJuedi'
import { syntheticBars } from './barsFixture'

const bars = syntheticBars()
const result = computeZhengyunJuedi(bars)
const series: any[] = juediSubSeries(bars)
const byName = (name: string) => series.find(s => s.name === name)

const lineData = (values: (number | null)[]) => values.map(v => (v == null ? '-' : v))
const signalData = (values: (number | null)[]) => values.map(v => (v == null || v === 0 ? null : v))

describe('juediSubSeries 线条与参考线', () => {
  it('AA / A1~A6 / A / 卖 为对应颜色的 line, 数据与公式输出逐根一致', () => {
    const expectations: [string, (number | null)[], string][] = [
      ['AA', result.aa, '#FFFF00'],
      ['A1', result.a1, '#FFDDCC'],
      ['A2', result.a2, '#FFBBCC'],
      ['A3', result.a3, '#FF99CC'],
      ['A4', result.a4, '#FF55CC'],
      ['A5', result.a5, '#FF33CC'],
      ['A6', result.a6, '#FF11CC'],
      ['A', result.a, '#FF00CC'],
      ['卖', result.maiY, '#00FF00'],
    ]
    for (const [name, values, color] of expectations) {
      const s = byName(name)
      expect(s?.type, name).toBe('line')
      expect(s?.lineStyle?.color, name).toBe(color)
      expect(s?.data, name).toEqual(lineData(values))
      expect(s?.animation, name).toBe(false)
    }
  })

  it('10 / 80 为常量参考线', () => {
    expect(byName('10')?.data).toEqual(bars.map(() => 10))
    expect(byName('80')?.data).toEqual(bars.map(() => 80))
  })
})

describe('juediSubSeries STICKLINE 趋势柱', () => {
  it('按 VAR17-VAR18 符号染红/绿, 相等不画', () => {
    const s = byName('趋势柱')
    expect(s?.type).toBe('custom')
    const expected: any[] = []
    result.var17.forEach((v17, i) => {
      const v18 = result.var18[i]
      if (v17 == null || v18 == null || v17 === v18) return
      expected.push({ value: [i, v17, v18], itemStyle: { color: v17 > v18 ? '#FF0000' : '#00FF00' } })
    })
    expect(s?.data).toEqual(expected)
    expect(expected.length).toBeGreaterThan(0) // 合成数据确实覆盖了红绿两态
  })
})

describe('juediSubSeries 信号柱与图标', () => {
  it('信号柱 0 值不画 (null)', () => {
    const bars4: [string, (number | null)[], string][] = [
      ['关注', result.guanzhu, '#00FF00'],
      ['极品底', result.jipindi, '#FF0000'],
      ['买', result.mai, '#FF00FF'],
      ['郑云绝底', result.juedi1, '#FF0000'],
      ['郑云绝底2', result.juedi2, '#FF0000'],
    ]
    for (const [name, values, color] of bars4) {
      const s = byName(name)
      expect(s?.type, name).toBe('bar')
      expect(s?.itemStyle?.color, name).toBe(color)
      expect(s?.data, name).toEqual(signalData(values))
    }
  })

  it('DRAWICON 按图标号拆成三个 scatter, 落点与公式一致', () => {
    for (const kind of [1, 5, 7] as const) {
      const s = byName(`图标${kind}`)
      expect(s?.type, `图标${kind}`).toBe('scatter')
      expect(s?.data, `图标${kind}`).toEqual(
        result.icons.filter(ic => ic.kind === kind).map(ic => [ic.index, ic.y]),
      )
    }
  })

  it('series 数量与设计一致 (2 参考线 + 8 线条 + 卖 + 5 信号柱 + 趋势柱 + 3 图标)', () => {
    expect(series).toHaveLength(20)
  })
})

describe('juediSubInfo 悬停信息栏', () => {
  it('无数据或索引非法时为空', () => {
    expect(juediSubInfo(undefined, 0)).toEqual([])
    expect(juediSubInfo(bars as TdxBar[], -1)).toEqual([])
  })

  it('按索引给出 AA/A1/A/VAR17/VAR18/买/卖/关注', () => {
    const idx = bars.length - 1
    const items = juediSubInfo(bars, idx)
    expect(items.map(i => i.label)).toEqual(['AA', 'A1', 'A', 'VAR17', 'VAR18', '买', '卖', '关注'])
    expect(items.find(i => i.label === 'AA')?.value).toBe((result.aa[idx] as number).toFixed(1))
    expect(items.find(i => i.label === 'VAR18')?.value).toBe((result.var18[idx] as number).toFixed(1))
    expect(items.find(i => i.label === '买')?.value).toBe(result.mai[idx] === 10 ? '买' : '—')
    expect(items.find(i => i.label === '卖')?.value).toBe(result.maiY[idx] === 85 ? '卖' : '—')
    expect(items.find(i => i.label === '关注')?.value).toBe(result.guanzhu[idx] === 10 ? '关注' : '—')
  })
})
