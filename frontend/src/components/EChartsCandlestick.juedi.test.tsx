// @vitest-environment jsdom
// 集成接线测试: 「绝底」副图必须按 SUB_CHARTS 既有机制挂到自己的 grid 上,
// 且未激活时完全不参与渲染 (默认关闭、零开销)。
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EChartsCandlestick, type OHLC } from './EChartsCandlestick'

const chart = vi.hoisted(() => ({
  setOption: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
  dispatchAction: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  getZr: () => ({ on: vi.fn(), off: vi.fn() }),
  getOption: () => ({ dataZoom: [{ start: 0, end: 100 }] }),
  containPixel: () => false,
  convertFromPixel: () => [0, 0],
}))

vi.mock('echarts', () => ({ init: () => chart }))

const DAYS = Array.from({ length: 120 }, (_, i) =>
  new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10))

/** 120 根确定性日线: 覆盖 60 根窗口与递归指标预热, 保证公式输出有值 */
const rows = (): OHLC[] => DAYS.map((date, i) => {
  const base = 10 + i * 0.02 + Math.sin(i / 7) * 0.6 + Math.sin(i / 23) * 1.2
  return { date, open: base, high: base + 0.2, low: base - 0.2, close: base, volume: 1000 + (i % 7) * 137 }
})

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  chart.setOption.mockClear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

function lastOption(): any {
  return chart.setOption.mock.calls.at(-1)?.[0]
}

async function render(activeIndicators: string[]) {
  await act(async () => root.render(
    <EChartsCandlestick
      data={rows()}
      symbol="600000"
      height={400}
      showInfoBar={false}
      visibleBars="all"
      activeIndicators={activeIndicators}
    />,
  ))
}

it('激活「绝底」时公式 series 挂到第 1 个副图 grid, 且趋势柱/图标齐备', async () => {
  await render(['juedi'])
  const option = lastOption()

  const aa = option.series.find((s: any) => s.name === 'AA')
  expect(aa?.type).toBe('line')
  expect(aa?.xAxisIndex).toBe(1)
  expect(aa?.yAxisIndex).toBe(1)
  expect(option.series.some((s: any) => s.name === '趋势柱' && s.type === 'custom')).toBe(true)
  expect(option.series.some((s: any) => s.name === '图标1' && s.type === 'scatter')).toBe(true)
  // 主图 grid + 1 个副图 grid
  expect(option.grid).toHaveLength(2)
})

it('未激活时副图不渲染 (默认关闭、零开销)', async () => {
  await render([])
  const option = lastOption()
  expect(option.series.some((s: any) => s.name === 'AA')).toBe(false)
  expect(option.grid).toHaveLength(1)
})

it('「绝底」与成交量同时开启时, 绝底紧贴主图 (成交量在其下方) —— 对齐通达信附图位置', async () => {
  await render(['vol', 'juedi'])
  const option = lastOption()

  // 主图 grid 0; 绝底 → grid 1; 成交量 → grid 2
  expect(option.series.find((s: any) => s.name === 'AA')?.xAxisIndex).toBe(1)
  expect(option.series.find((s: any) => s.name === '成交量')?.xAxisIndex).toBe(2)
  expect(option.grid).toHaveLength(3)

  // 信息栏 graphic 必须与 grid 同序, 否则标签/数值会贴到错误的副图上
  const sep = (key: string) => option.graphic.find((g: any) => g.id === `sub-sep-${key}`)
  expect(sep('juedi')?.shape.y1).toBeLessThan(sep('vol')?.shape.y1)
})
