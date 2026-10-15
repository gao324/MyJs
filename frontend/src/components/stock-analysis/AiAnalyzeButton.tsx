import { useEffect, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { toast } from '@/components/Toast'
import { startAnalysis } from '@/lib/stockAnalysisStore'

/**
 * 个股详情弹窗的一键 AI 分析按钮。
 *
 * 复用个股分析页同一条链路: startAnalysis() 建流式任务并交给全局对话框宿主
 * (StockAnalysisHost 挂在 Layout, 与财务/策略分析的宿主并列), 本组件不自己维护进度状态。
 * AI 未配置时置灰并提示去「设置 → AI」, 避免点了没反应。
 */
export function AiAnalyzeButton({ symbol, name }: { symbol: string; name?: string | null }) {
  // null = 尚未查明; 此时不禁用, 点击时再查一次 (避免慢请求期间按钮不可用)
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    let alive = true
    api.strategyAiStatus()
      .then(s => { if (alive) setConfigured(s.configured) })
      .catch(() => { if (alive) setConfigured(null) })
    return () => { alive = false }
  }, [])

  const handleClick = async () => {
    if (starting) return
    setStarting(true)
    try {
      if (configured == null) {
        const status = await api.strategyAiStatus()
        setConfigured(status.configured)
        if (!status.configured) {
          toast('请先在「设置 → AI」配置分析模型', 'error')
          return
        }
      }
      const result = await startAnalysis(symbol, name ?? '')
      if (result.error) toast(result.error, 'error')
    } catch {
      toast('AI 分析启动失败, 请检查「设置 → AI」配置', 'error')
    } finally {
      setStarting(false)
    }
  }

  const unconfigured = configured === false
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={unconfigured || starting}
      title={unconfigured ? '请先在「设置 → AI」配置分析模型' : 'AI 分析该股'}
      aria-label={`AI 分析 ${symbol}`}
      className="rounded-btn p-1.5 text-sky-400 transition-colors cursor-pointer hover:bg-sky-400/10 disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
    </button>
  )
}
