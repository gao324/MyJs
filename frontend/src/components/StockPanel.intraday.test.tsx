// @vitest-environment jsdom
// 「弹窗打开时不默认展示分时」的行为测试。
//
// 现状: StockPanel 的 showIntraday 会在一拿到日K就自动选中最新日期并渲染分时面板,
// 于是个股详情弹窗一打开就是「日K + 分时」并排。
// 目标: 新增 intradayInitiallyOpen=false 时默认收起分时, 单击某根 K 线后再展开该日分时;
//       不传该参数时保持原行为 (其它页面不变)。
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const PICKED_DATE = '2026-10-13'
const ROWS = [
  { date: '2026-10-12', open: 10, high: 10.5, low: 9.8, close: 10.2, volume: 100 },
  { date: PICKED_DATE, open: 10.2, high: 10.8, low: 10, close: 10.6, volume: 120 },
]

vi.mock('@/components/StockDailyKChart', () => ({
  getDefaultRange: () => ({ start: '2026-04-14', end: '2026-10-14' }),
  toOHLC: (rows: any[]) => rows,
  StockDailyKChart: ({ onDateClick }: any) => (
    <button data-testid="pick-date" onClick={() => onDateClick?.(PICKED_DATE)}>K</button>
  ),
}))
vi.mock('@/components/StockIntradayChart', () => ({ StockIntradayChart: () => <div data-testid="intraday" /> }))
vi.mock('@/components/StockInfoBar', () => ({ StockInfoBar: () => <div data-testid="info-bar" /> }))
vi.mock('@/lib/kline', () => ({
  DEFAULT_INTRADAY_DAYS: 10,
  klineDailyQueryOptions: (symbol: string) => ({
    queryKey: ['test-kline', symbol],
    queryFn: async () => ({ rows: ROWS, name: '测试股', stock_info: {} }),
  }),
  klineMinuteQueryOptions: () => ({ queryKey: ['test-minute'], queryFn: async () => ({ rows: [] }) }),
  klineMinuteRangeQueryOptions: () => ({ queryKey: ['test-minute-range'], queryFn: async () => ({ rows: [] }) }),
}))
vi.mock('@/lib/useFinancials', () => ({
  financialMetricsQueryOptions: () => ({ queryKey: ['test-fin'], queryFn: async () => ({}) }),
  useFinancialMetrics: () => ({ data: undefined }),
}))
vi.mock('@/lib/useSharedQueries', () => ({ useCapabilities: () => ({ data: undefined }) }))
vi.mock('@/lib/neighborPrefetch', () => ({ scheduleNeighborPrefetch: () => () => {} }))

const { StockPanel } = await import('./StockPanel')

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
let qc: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.unstubAllGlobals()
})

async function render(props: Record<string, unknown>) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <StockPanel symbol="600000" {...props} />
      </QueryClientProvider>,
    )
  })
  // 等日K query 落地
  await act(async () => { await new Promise(r => setTimeout(r, 20)) })
}

const has = (id: string) => !!host.querySelector(`[data-testid="${id}"]`)

it('不传 intradayInitiallyOpen 时保持原行为: 自动展开最新一日分时', async () => {
  await render({})
  expect(has('pick-date')).toBe(true)
  expect(has('intraday')).toBe(true)
})

it('intradayInitiallyOpen=false: 打开时不展示分时, 单击 K 线后再展开', async () => {
  await render({ intradayInitiallyOpen: false })
  expect(has('pick-date')).toBe(true)
  expect(has('intraday')).toBe(false)

  await act(async () => {
    host.querySelector<HTMLButtonElement>('[data-testid="pick-date"]')!.click()
  })
  expect(has('intraday')).toBe(true)
})

it('展开后仍可用「收起分时图」按钮关掉', async () => {
  await render({ intradayInitiallyOpen: false })
  await act(async () => {
    host.querySelector<HTMLButtonElement>('[data-testid="pick-date"]')!.click()
  })
  expect(has('intraday')).toBe(true)

  await act(async () => {
    host.querySelector<HTMLButtonElement>('[aria-label="收起分时图"]')!.click()
  })
  expect(has('intraday')).toBe(false)
})
