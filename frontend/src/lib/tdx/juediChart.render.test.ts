// 真实 ECharts 渲染回归 (SVG/SSR): 覆盖 jsdom + mock echarts 测不到的部分 ——
// custom series 的 renderItem / api.size / api.visual 是否真能出图、是否有废弃 API 警告。
// 曾经的盲点: renderItem 里用已废弃的 api.style(), mock 测试全绿但真机控制台报 DEPRECATED。
import { expect, it } from 'vitest'
import * as echarts from 'echarts'
import { juediSubSeries } from './juediChart'
import { syntheticBars } from './barsFixture'

it('真实 echarts 渲染「绝底」副图 series: 不抛错、有图形输出、无废弃 API 警告', () => {
  const bars = syntheticBars(120)
  const series = juediSubSeries(bars).map(s => ({ ...s, xAxisIndex: 0, yAxisIndex: 0 }))

  const warnings: string[] = []
  const originalWarn = console.warn
  console.warn = (...args: unknown[]) => { warnings.push(args.map(String).join(' ')) }

  let svg = ''
  const chart = echarts.init(null as any, null, { renderer: 'svg', ssr: true, width: 1000, height: 160 })
  try {
    chart.setOption({
      animation: false,
      grid: { left: 60, right: 20, top: 8, height: 140 },
      xAxis: { type: 'category', data: bars.map(b => b.date), boundaryGap: true },
      yAxis: { type: 'value', scale: true },
      series,
    } as any)
    svg = chart.renderToSVGString()
  } finally {
    chart.dispose()
    console.warn = originalWarn
  }

  // 线条 + 趋势柱 + 信号柱都要真的画出图形, 而不是只有坐标轴
  expect((svg.match(/<path/g) ?? []).length).toBeGreaterThan(10)
  expect(svg.length).toBeGreaterThan(2000)
  expect(warnings.filter(w => w.includes('DEPRECATED'))).toEqual([])
}, 60_000)
