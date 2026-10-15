// @vitest-environment jsdom
// 回归: 单击主图「任意位置」都要能选中该日, 而不是只有压在蜡烛实体上才算。
//
// 根因(修复前): 选中日期只走 chart.on('click') 且要求 seriesName === 'K',
// 而 candlestick 的命中区就是画出来的实体矩形 + 1~2px 影线 —— 小实体/十字星、
// 实体上方的空白处点了都没有事件。
// 修复: 与双击设预警 (handlePriceDoubleClick) 同口径, 用 zr 层像素→类目映射覆盖整个主图 grid。
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EChartsCandlestick, type OHLC } from './EChartsCandlestick'

const chart = vi.hoisted(() => ({
  zrHandlers: {} as Record<string, (event?: any) => void>,
  insideGrid: true,
  xValue: 10,
  setOption: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
  dispatchAction: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  containPixel: vi.fn(() => true),
  convertFromPixel: vi.fn(() => [10, 12.3]),
  getOption: () => ({ dataZoom: [{ start: 0, end: 100 }] }),
}))

vi.mock('echarts', () => ({
  init: () => ({
    setOption: chart.setOption,
    on: chart.on,
    off: chart.off,
    dispatchAction: chart.dispatchAction,
    resize: chart.resize,
    dispose: chart.dispose,
    containPixel: chart.containPixel,
    convertFromPixel: chart.convertFromPixel,
    getOption: chart.getOption,
    getZr: () => ({
      on: (name: string, cb: (event?: any) => void) => { chart.zrHandlers[name] = cb },
      off: (name: string) => { delete chart.zrHandlers[name] },
    }),
  }),
}))

const DAYS = Array.from({ length: 40 }, (_, i) =>
  new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10))
/** 小实体十字星: 开=收, 修复前几乎点不中 */
const rows = (): OHLC[] => DAYS.map(date => ({ date, open: 10, high: 10.1, low: 9.9, close: 10, volume: 100 }))

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  chart.zrHandlers = {}
  chart.setOption.mockClear()
  chart.containPixel.mockReturnValue(true)
  chart.convertFromPixel.mockReturnValue([10, 12.3])
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

async function render(onDateClick: (date: string) => void) {
  await act(async () => root.render(
    <EChartsCandlestick data={rows()} symbol="600000" height={400} visibleBars="all" onDateClick={onDateClick} />,
  ))
}

it('点击主图空白列 (未压在蜡烛实体上) 也能选中最近的交易日', async () => {
  const picked: string[] = []
  await render(d => picked.push(d))

  // 鼠标落在第 10 列附近 (像素 → 类目 10.4 由 convertFromPixel 给出), 修复前这里不会触发任何回调
  await act(async () => { chart.zrHandlers.click?.({ offsetX: 300, offsetY: 120 }) })

  expect(picked).toEqual([DAYS[10]])
})

it('点击主图以外的区域 (副图/坐标轴外) 不选中', async () => {
  const picked: string[] = []
  await render(d => picked.push(d))

  chart.containPixel.mockReturnValue(false)
  await act(async () => { chart.zrHandlers.click?.({ offsetX: 300, offsetY: 460 }) })

  expect(picked).toEqual([])
})

it('像素映射越界时按边界收敛, 不抛错也不乱选', async () => {
  const picked: string[] = []
  await render(d => picked.push(d))

  chart.convertFromPixel.mockReturnValue([999, 12.3])
  await act(async () => { chart.zrHandlers.click?.({ offsetX: 999, offsetY: 120 }) })

  expect(picked).toEqual([DAYS[DAYS.length - 1]])
})
