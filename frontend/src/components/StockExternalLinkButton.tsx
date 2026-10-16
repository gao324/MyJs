/**
 * 个股外链 — 在外部行情 App (通达信 / 同花顺, 设置里二选一) 中打开该股。
 *
 * 唤起是否成功, 网页侧拿不到可靠信号, 只能用「跳转后页面有没有被切走」近似判断:
 * 1.5s 内页面仍是 visible 就认为没唤起, 提示代码已复制、可粘贴到 App 内搜索。
 *
 * 这个近似必然有假阴性 —— iOS Safari 的「在"通达信"中打开?」确认框期间页面不失焦。
 * 因此降级文案一律是软性的「若未自动跳转」, 不能断言失败。
 */
import { useCallback } from 'react'
import type { MouseEvent, ReactNode } from 'react'
import { ExternalLink } from 'lucide-react'
import {
  buildStockExternalUrl,
  isTemplateSchemeAllowed,
  loadStockExternalConfig,
  EXTERNAL_TARGET_LABELS,
} from '@/lib/stock-external-link'
import { copyText } from '@/lib/clipboard'
import { toast } from '@/components/Toast'

/** 跳转后等待多久判定「没唤起」。太短会把慢启动误判, 太长让用户白等。 */
const LAUNCH_WATCH_MS = 1500

const HTTP_RE = /^https?:/i

/** 从 symbol 取纯 6 位代码 (降级时复制给用户在 App 内搜索) */
function codeOf(symbol: string): string {
  return symbol.split('.')[0]
}

/**
 * 唤起外部 App; 判定未唤起时提示代码已复制。
 * 普通网页链接走新标签打开, 不存在「唤起不了」的问题, 不做降级。
 */
export function launchStockExternal(url: string, label: string, symbol: string): void {
  if (HTTP_RE.test(url)) {
    window.open(url, '_blank', 'noopener,noreferrer')
    return
  }

  const code = codeOf(symbol)
  let left = false
  const markLeft = () => { left = true }
  document.addEventListener('visibilitychange', markLeft, { once: true })
  window.addEventListener('pagehide', markLeft, { once: true })

  try {
    window.location.href = url
  } catch {
    // 少数环境对未知 scheme 直接抛错, 走下面的超时降级
  }

  window.setTimeout(async () => {
    document.removeEventListener('visibilitychange', markLeft)
    window.removeEventListener('pagehide', markLeft)
    if (left || document.visibilityState === 'hidden') return
    const copied = await copyText(code)
    toast(
      copied
        ? `若未自动跳转，请在${label}中搜索 ${code}（代码已复制）`
        : `若未自动跳转，请在${label}中搜索 ${code}`,
      'success',
    )
  }, LAUNCH_WATCH_MS)
}

export interface StockExternalConfigReader {
  /** 当前目标 App 的中文名 (用于 title / aria-label / 降级文案) */
  label: string
  /** 当前目标是否配了可用模板 (非空且 scheme 合法); false 时 build 恒为 null */
  configured: boolean
  /** 为某只股票构造外链; null = 未配置 / 模板 scheme 非法 / symbol 非法 */
  build: (symbol: string) => string | null
}

/**
 * 读取当前外链配置, 给出按股票构造 URL 的能力。
 * 每次渲染都重读 localStorage (同步读, 很轻) —— 与 StockInfoBar 原实现同款,
 * 这样设置页改完回来立即生效, 不需要额外的订阅机制。
 * 列表场景请用这个 hook 读一次, 再对每行调 build, 避免逐行读 localStorage。
 */
export function useStockExternalConfig(): StockExternalConfigReader {
  const { target, templates } = loadStockExternalConfig()
  const template = templates[target]
  const build = useCallback((symbol: string) => buildStockExternalUrl(template, symbol), [template])
  return {
    label: EXTERNAL_TARGET_LABELS[target],
    configured: isTemplateSchemeAllowed(template) && template.trim() !== '',
    build,
  }
}

export interface StockExternal {
  /** 按当前启用目标构造出的外链; null = 未配置 / 模板 scheme 非法 / symbol 非法 */
  url: string | null
  /** 当前目标 App 的中文名 */
  label: string
  /** 纯 6 位代码 */
  code: string
  /** 打开外链; url 为 null 时不做事 */
  launch: () => void
}

/** 单只股票的打开动作 (详情弹窗 / 信息条的按钮用) */
export function useStockExternal(symbol: string): StockExternal {
  const { label, build } = useStockExternalConfig()
  const url = build(symbol)
  const code = codeOf(symbol)
  const launch = useCallback(() => {
    if (url) launchStockExternal(url, label, symbol)
  }, [url, label, symbol])
  return { url, label, code, launch }
}

export interface StockExternalLinkButtonProps {
  symbol: string
  /** 图标尺寸/颜色/圆角; 默认与个股详情弹窗工具栏的图标按钮一致 */
  className?: string
  /** 图标本身的尺寸; 各接入点工具栏密度不同 (信息条比详情弹窗更紧凑) */
  iconClassName?: string
  /** 追加在图标后的内容 */
  children?: ReactNode
}

/**
 * 外链图标按钮。未配置外链时不渲染 —— 调用方不需要自己判断。
 */
export function StockExternalLinkButton({
  symbol,
  className = 'p-1.5 rounded-btn text-secondary hover:text-foreground hover:bg-elevated transition-colors cursor-pointer',
  iconClassName = 'h-4 w-4',
  children,
}: StockExternalLinkButtonProps) {
  const { url, label, launch } = useStockExternal(symbol)
  if (!url) return null

  const onClick = (e: MouseEvent) => {
    // 保留 href: 移动端长按仍可复制链接。走自己的 launch 以便统一降级处理。
    e.preventDefault()
    launch()
  }

  return (
    <a
      href={url}
      onClick={onClick}
      title={`在${label}中打开 ${symbol}`}
      aria-label={`在${label}中打开 ${symbol}`}
      className={className}
    >
      <ExternalLink className={iconClassName} />
      {children}
    </a>
  )
}
