/**
 * 「郑云绝底」副图的 ECharts series 与悬停信息栏映射。
 *
 * 只产出 option 对象、不 import echarts，保持本层与图表运行时解耦并可直接单测；
 * xAxisIndex/yAxisIndex 由 EChartsCandlestick 的副图装配统一补齐。
 * 颜色与图形对应关系见设计文档 §4.4（通达信字面色值）。
 */
import type { Series } from './primitives'
import { computeZhengyunJuedi, type JuediIcon, type TdxBar } from './zhengyunJuedi'

const COLOR = {
  red: '#FF0000',
  green: '#00FF00',
  magenta: '#FF00FF',
  yellow: '#FFFF00',
  aLine: '#FF00CC',
  refLine: '#71717A',
  bronze: '#CFB53B',
  paleYellow: '#FFFFCC',
}

/** A1~A6 的渐变色（原公式 COLORFFDDCC → COLORFF11CC） */
const A_LINE_COLORS = ['#FFDDCC', '#FFBBCC', '#FF99CC', '#FF55CC', '#FF33CC', '#FF11CC']

/** A 与 A1 共用的主色（信息栏取色） */
const A_COLOR = COLOR.aLine

export interface SubInfoItem {
  label: string
  color: string
  value: string
}

const lineData = (values: Series) => values.map(v => (v == null ? '-' : v))
const signalData = (values: Series) => values.map(v => (v == null || v === 0 ? null : v))

function line(name: string, values: Series, color: string, overrides: Record<string, unknown> = {}): any {
  return {
    name,
    type: 'line',
    data: lineData(values),
    smooth: false,
    symbol: 'none',
    animation: false,
    lineStyle: { width: 1, color },
    itemStyle: { color },
    ...overrides,
  }
}

function signalBar(name: string, values: Series, color: string, barWidth = '40%'): any {
  return {
    name,
    type: 'bar',
    data: signalData(values),
    barWidth,
    animation: false,
    itemStyle: { color },
  }
}

/** DRAWICON: 同图标号的落点合成一个 scatter（符号与颜色区分，替代通达信内置位图） */
function iconSeries(name: string, icons: JuediIcon[], kind: JuediIcon['kind'], color: string, symbol: string): any {
  return {
    name,
    type: 'scatter',
    symbol,
    symbolSize: 8,
    silent: true,
    animation: false,
    z: 5,
    itemStyle: { color },
    data: icons.filter(ic => ic.kind === kind).map(ic => [ic.index, ic.y]),
  }
}

/** STICKLINE(VAR17-VAR18>0/<0, VAR17, VAR18): 逐根竖柱, 红=快线在上, 绿=慢线在上 */
function stickSeries(bars: readonly TdxBar[]): any {
  const r = computeZhengyunJuedi(bars)
  const data: any[] = []
  r.var17.forEach((v17, i) => {
    const v18 = r.var18[i]
    if (v17 == null || v18 == null || v17 === v18) return
    data.push({ value: [i, v17, v18], itemStyle: { color: v17 > v18 ? COLOR.red : COLOR.green } })
  })
  return {
    name: '趋势柱',
    type: 'custom',
    data,
    silent: true,
    animation: false,
    z: 1,
    renderItem: (_params: any, api: any) => {
      const index = api.value(0) as number
      const [x, fastY] = api.coord([index, api.value(1)])
      const [, slowY] = api.coord([index, api.value(2)])
      // 柱宽取一个类目宽度的 60%; 高度至少 1px, 避免上下端重合时不可见
      const half = Math.max(((api.size([1, 0]) as number[])[0] ?? 0) * 0.3, 1)
      return {
        type: 'rect',
        shape: {
          x: x - half,
          y: Math.min(fastY, slowY),
          width: half * 2,
          height: Math.max(Math.abs(slowY - fastY), 1),
        },
        // 颜色取自 data item 的 itemStyle (红/绿按 VAR17-VAR18 符号); api.style() 已废弃
        style: { fill: api.visual('color') as string },
      }
    },
  }
}

/** 副图全部 series（顺序: 参考线 → 趋势柱 → 线条 → 信号柱 → 图标） */
export function juediSubSeries(bars: readonly TdxBar[]): any[] {
  const r = computeZhengyunJuedi(bars)
  const refLine = (value: number) =>
    line(String(value), new Array<number>(bars.length).fill(value), COLOR.refLine, {
      lineStyle: { width: 1, color: COLOR.refLine, type: 'dashed' },
      silent: true,
      z: 0,
    })

  return [
    refLine(10),
    refLine(80),
    stickSeries(bars),
    line('AA', r.aa, COLOR.yellow),
    line('A1', r.a1, A_LINE_COLORS[0]),
    line('A2', r.a2, A_LINE_COLORS[1]),
    line('A3', r.a3, A_LINE_COLORS[2]),
    line('A4', r.a4, A_LINE_COLORS[3]),
    line('A5', r.a5, A_LINE_COLORS[4]),
    line('A6', r.a6, A_LINE_COLORS[5]),
    line('A', r.a, A_COLOR),
    line('卖', r.maiY, COLOR.green),
    signalBar('关注', r.guanzhu, COLOR.green),
    signalBar('极品底', r.jipindi, COLOR.red),
    signalBar('买', r.mai, COLOR.magenta, '60%'),
    signalBar('郑云绝底', r.juedi1, COLOR.red),
    signalBar('郑云绝底2', r.juedi2, COLOR.red),
    iconSeries('图标1', r.icons, 1, COLOR.red, 'triangle'),
    iconSeries('图标5', r.icons, 5, COLOR.bronze, 'diamond'),
    iconSeries('图标7', r.icons, 7, COLOR.paleYellow, 'circle'),
  ]
}

/** 悬停信息栏: 取当前数据索引的各输出值（副图副标题右侧一行） */
export function juediSubInfo(bars: readonly TdxBar[] | undefined, index: number): SubInfoItem[] {
  if (!bars || index < 0 || index >= bars.length) return []
  const r = computeZhengyunJuedi(bars)
  const num = (v: number | null) => (v == null ? '—' : v.toFixed(1))
  return [
    { label: 'AA', color: COLOR.yellow, value: num(r.aa[index]) },
    { label: 'A1', color: A_LINE_COLORS[0], value: num(r.a1[index]) },
    { label: 'A', color: A_COLOR, value: num(r.a[index]) },
    { label: 'VAR17', color: COLOR.red, value: num(r.var17[index]) },
    { label: 'VAR18', color: COLOR.green, value: num(r.var18[index]) },
    { label: '买', color: COLOR.magenta, value: r.mai[index] === 10 ? '买' : '—' },
    { label: '卖', color: COLOR.green, value: r.maiY[index] === 85 ? '卖' : '—' },
    { label: '关注', color: COLOR.green, value: r.guanzhu[index] === 10 ? '关注' : '—' },
  ]
}
