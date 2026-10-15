// @vitest-environment jsdom
// 一键 AI 分析按钮的行为测试: 未配置 AI 时禁用并指向设置; 已配置时直接启动分析并交给全局对话框宿主;
// 启动失败/任务已满等错误必须显式提示, 不能静默。
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  strategyAiStatus: vi.fn(),
  startAnalysis: vi.fn(),
  toast: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ api: { strategyAiStatus: mocks.strategyAiStatus } }))
vi.mock('@/lib/stockAnalysisStore', () => ({ startAnalysis: mocks.startAnalysis }))
vi.mock('@/components/Toast', () => ({ toast: mocks.toast }))

const { AiAnalyzeButton } = await import('./AiAnalyzeButton')

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  mocks.strategyAiStatus.mockReset()
  mocks.startAnalysis.mockReset()
  mocks.toast.mockReset()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

async function render() {
  await act(async () => {
    root.render(<AiAnalyzeButton symbol="600000.SH" name="测试股" />)
  })
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
}

const button = () => host.querySelector<HTMLButtonElement>('button')!

it('AI 未配置: 按钮禁用且提示去设置', async () => {
  mocks.strategyAiStatus.mockResolvedValue({ configured: false })
  await render()

  expect(button().disabled).toBe(true)
  expect(button().getAttribute('title')).toContain('设置')
})

it('AI 已配置: 点击直接启动该股分析', async () => {
  mocks.strategyAiStatus.mockResolvedValue({ configured: true })
  mocks.startAnalysis.mockResolvedValue({ id: 'stask_1' })
  await render()

  expect(button().disabled).toBe(false)
  await act(async () => { button().click() })
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })

  expect(mocks.startAnalysis).toHaveBeenCalledWith('600000.SH', '测试股')
  expect(mocks.toast).not.toHaveBeenCalled()
})

it('AI 状态查询失败: 点击时提示错误且不启动分析', async () => {
  mocks.strategyAiStatus.mockRejectedValue(new Error('network down'))
  await render()

  await act(async () => { button().click() })
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })

  expect(mocks.startAnalysis).not.toHaveBeenCalled()
  expect(mocks.toast).toHaveBeenCalled()
  const [msg, kind] = mocks.toast.mock.calls[0]
  expect(String(msg)).toContain('AI')
  expect(kind).toBe('error')
})

it('启动返回错误 (如任务数超限): 原样提示', async () => {
  mocks.strategyAiStatus.mockResolvedValue({ configured: true })
  mocks.startAnalysis.mockResolvedValue({ error: '同时进行的个股分析任务不能超过 3 个,请等待现有任务完成' })
  await render()

  await act(async () => { button().click() })
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })

  expect(mocks.toast).toHaveBeenCalledWith('同时进行的个股分析任务不能超过 3 个,请等待现有任务完成', 'error')
})
