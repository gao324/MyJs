// @vitest-environment jsdom
// 研报完成态测试: done 事件带 reportUrl 时渲染「打开报告」; 降级报告显示降级提示;
// 旧引擎(done 无 reportUrl)不渲染按钮。
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { StockAnalysisDialog } from './StockAnalysisDialog'
import type { ActiveTask } from '@/lib/stockAnalysisStore'

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

function doneTask(over: Partial<ActiveTask> = {}): ActiveTask {
  return {
    id: 'stask_1', symbol: '600519.SH', name: '贵州茅台', focus: '',
    phase: 'done', content: '- Phase 1/4 数据采集中', error: '',
    meta: { summary: '深度研报生成中(通常 3-8 分钟)' },
    createdAt: Date.now(),
    ...over,
  }
}

async function renderDialog(task: ActiveTask) {
  await act(async () => {
    root.render(<StockAnalysisDialog mode="active" minimized={false} task={task} />)
  })
  await act(async () => { await new Promise(r => setTimeout(r, 10)) })
}

describe('StockAnalysisDialog 研报完成态', () => {
  it('done 事件带 reportUrl 时渲染打开报告按钮', async () => {
    await renderDialog(doneTask({ reportId: 'sr1', reportUrl: '/api/stock-reports/sr1/html' }))
    const link = host.querySelector<HTMLAnchorElement>('a[href="/api/stock-reports/sr1/html"]')
    expect(link).not.toBeNull()
    expect(link!.textContent).toContain('打开报告')
  })

  it('降级报告同时显示降级提示', async () => {
    await renderDialog(doneTask({
      reportId: 'sr1', reportUrl: '/api/stock-reports/sr1/html', degraded: true,
    }))
    expect(host.textContent).toContain('存在降级项')
  })

  it('旧引擎(done 无 reportUrl)不渲染按钮', async () => {
    await renderDialog(doneTask())
    const links = [...host.querySelectorAll('a')].filter(a => a.textContent?.includes('打开报告'))
    expect(links.length).toBe(0)
  })
})
