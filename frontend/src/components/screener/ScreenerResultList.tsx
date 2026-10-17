/**
 * 策略结果清单 — 手机端替代宽表格 (<768px)。
 *
 * 手机上不做列配置 / 迷你图 / 冻结列 / 排序表头: 一行一票, 整行点击直接跳到
 * 外部行情 App (通达信 / 同花顺, 设置里二选一, 未配置则行不可点)。
 *
 * 字段口径与 ScreenerTable 一致: r.change_pct 为小数制, 经 fmtPct 转百分数
 * (CONTRIBUTING §3.1); 股息率为 GM 截面值 (小数制, 快照无该标的为 null → 「—」,
 * 口径说明见 DIVIDEND_YIELD_HINT); 失效行 (今日曾命中、当前不命中) 用 opacity
 * 弱化, 与表格同款处理。
 */
import { ArrowUpRight } from 'lucide-react'
import { fmtPct, priceColorClass } from '@/lib/format'
import { boardTag } from '@/components/stock-table/primitives'
import { launchStockExternal, useStockExternalConfig } from '@/components/StockExternalLinkButton'
import { DIVIDEND_YIELD_HINT } from '@/lib/screener-columns'

interface Props {
  /** 展示行 (已由页面完成筛选 / 排序), 字段: symbol / name / change_pct / dividend_yield / _expired */
  rows: any[]
}

export function ScreenerResultList({ rows }: Props) {
  // 读一次配置, 逐行只做纯字符串替换 —— 避免每行都读 localStorage
  const { label, configured, build } = useStockExternalConfig()

  return (
    <>
      {/* 没配外链时行不可点。不提示的话用户只会觉得「点了没反应」, 必须给出原因和去处 */}
      {!configured && (
        <div className="mb-2 rounded-btn border border-warning/30 bg-warning/[0.06] px-3 py-2 text-[11px] leading-relaxed text-warning">
          未配置外部行情 App，点击个股不会跳转。请到「设置 → 系统 → 个股详情外链」填入通达信或同花顺的 URL 模板。
        </div>
      )}
      <div className="overflow-hidden rounded-xl border border-border bg-surface/40">
        {rows.map((r: any) => {
          const url = build(r.symbol)
          const board = boardTag(r.symbol)
          const pctCls = priceColorClass(r.change_pct)
          const key = `${r.symbol}${r._expired ? '-expired' : ''}`
          const rowCls = `flex items-center gap-2 px-3 py-2.5 border-b border-border/50 last:border-0 ${
            r._expired ? 'opacity-40' : ''
          }`
          const dividendYield = r.dividend_yield != null && Number.isFinite(r.dividend_yield)
            ? `${(Number(r.dividend_yield) * 100).toFixed(2)}%`
            : '—'
          const body = (
            <>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-[12px] font-medium text-foreground">{r.symbol}</span>
                  {board && (
                    <span className={`inline-flex items-center justify-center rounded border px-1 text-[9px] font-bold leading-tight ${board.color}`}>
                      {board.label}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 truncate text-[11px] text-secondary">{r.name || '—'}</div>
              </div>
              <span
                className="shrink-0 font-mono text-[10px] tabular-nums text-muted"
                title={DIVIDEND_YIELD_HINT}
              >
                股息 {dividendYield}
              </span>
              <span className={`shrink-0 font-mono text-[13px] font-medium tabular-nums ${pctCls}`}>
                {fmtPct(r.change_pct)}
              </span>
              {url && <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted" />}
            </>
          )

          // 未配置外链时行不可点 —— 点击无反馈比不可点更糟
          return url ? (
            <a
              key={key}
              href={url}
              onClick={(e) => {
                // 保留 href: 移动端长按仍可复制链接
                e.preventDefault()
                launchStockExternal(url, label, r.symbol)
              }}
              title={`在${label}中打开 ${r.symbol}`}
              className={`${rowCls} cursor-pointer active:bg-elevated/60`}
            >
              {body}
            </a>
          ) : (
            <div key={key} className={rowCls}>
              {body}
            </div>
          )
        })}
      </div>
    </>
  )
}
