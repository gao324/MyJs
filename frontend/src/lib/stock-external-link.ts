/**
 * 个股详情外链 — 可配置 URL 模板 + 证券代码, 跳到外部行情 App。
 *
 * 目标 App 二选一 (通达信 / 同花顺), 各自一份模板, 只启用当前目标那份。
 * 占位符保持独立, 用户自行排列 (市场前缀/后缀因站而异):
 *   {code}   纯 6 位代码, 如 000001
 *   {market} 市场前缀小写, 如 sz / sh / bj
 *   {symbol} 完整 symbol (含交易所后缀), 如 000001.SZ
 * 当前目标的模板留空 = 关闭外链。
 */
import { storage, type StockExternalConfig } from '@/lib/storage'

export type { StockExternalConfig }

/** 形状守卫: 只放行 6位数字 + 沪深北后缀 的 symbol (信息条场景下即股票) */
const STOCK_SYMBOL_RE = /^(\d{6})\.(SH|SZ|BJ)$/

/** 目标 App 的可选值, 顺序即设置页展示顺序 */
export const EXTERNAL_TARGETS = ['tdx', 'hexin'] as const
export type ExternalTarget = (typeof EXTERNAL_TARGETS)[number]

/** 目标 App 的中文名 (设置页与按钮共用, 单一权威) */
export const EXTERNAL_TARGET_LABELS: Record<ExternalTarget, string> = {
  tdx: '通达信',
  hexin: '同花顺',
}

const DEFAULT_TARGET: ExternalTarget = 'tdx'

/**
 * 外链允许的 scheme 白名单。模板可在设置里自由编辑, 这里是安全闸门:
 * http/https 是常规网页, tdx/hexin 是通达信 / 同花顺手机版的 App scheme。
 * 注意: tdx / hexin 两个字面量尚未真机实测确认; 若实测为其它字面量,
 * 改这一处即可 (设置里的模板模板串不用动)。
 */
const ALLOWED_SCHEMES = ['http', 'https', 'tdx', 'hexin']

function schemeAllowed(url: string): boolean {
  // 要求 scheme 后至少有一个非空白字符, 挡掉裸 "tdx:"
  const m = /^([a-z][a-z0-9+.-]*):(\S.*)$/i.exec(url)
  if (!m) return false
  const scheme = m[1].toLowerCase()
  if (!ALLOWED_SCHEMES.includes(scheme)) return false
  // http/https 仍要求 // 授权部分 (与旧行为一致, 不受本次放宽影响)
  return scheme === 'http' || scheme === 'https' ? m[2].startsWith('//') : true
}

function normalizeTarget(value: unknown): ExternalTarget {
  return EXTERNAL_TARGETS.includes(value as ExternalTarget)
    ? (value as ExternalTarget)
    : DEFAULT_TARGET
}

/**
 * 读取外链配置。
 * 优先新 key; 新 key 不存在时把旧版单模板 key 的值读入当前目标的槽位 ——
 * 非破坏性迁移, 旧 key 保留不删, 便于回退旧版本。
 */
export function loadStockExternalConfig(): StockExternalConfig {
  const raw: unknown = storage.stockExternalLinks.get(null)
  if (raw && typeof raw === 'object') {
    const o = raw as { target?: unknown; templates?: unknown }
    const t = (o.templates ?? {}) as Record<string, unknown>
    return {
      target: normalizeTarget(o.target),
      templates: {
        tdx: typeof t.tdx === 'string' ? t.tdx : '',
        hexin: typeof t.hexin === 'string' ? t.hexin : '',
      },
    }
  }
  return {
    target: DEFAULT_TARGET,
    templates: { tdx: storage.stockExternalTemplate.get(''), hexin: '' },
  }
}

export function saveStockExternalConfig(cfg: StockExternalConfig): void {
  storage.stockExternalLinks.set({
    target: normalizeTarget(cfg.target),
    templates: {
      tdx: (cfg.templates.tdx ?? '').trim(),
      hexin: (cfg.templates.hexin ?? '').trim(),
    },
  })
}

/**
 * 模板的 scheme 是否在白名单内。
 * 设置页用它给出即时提示 —— 否则模板写错 scheme 时按钮只会静默消失, 用户无从判断原因。
 * 空模板视为合法 (等于主动关闭外链)。
 */
export function isTemplateSchemeAllowed(template: string): boolean {
  const tpl = template.trim()
  return tpl ? schemeAllowed(tpl) : true
}

/**
 * 由模板与证券代码构造外链 URL。
 * scheme 不在白名单、模板为空或 symbol 形状非法时返回 null (即不渲染外链)。
 */
export function buildStockExternalUrl(template: string, symbol: string): string | null {
  const tpl = template.trim()
  if (!tpl) return null
  if (!schemeAllowed(tpl)) return null
  const m = symbol.match(STOCK_SYMBOL_RE)
  if (!m) return null
  const code = m[1]
  const market = m[2].toLowerCase()
  return tpl
    .replaceAll('{code}', code)
    .replaceAll('{market}', market)
    .replaceAll('{symbol}', symbol)
}

/** 按当前启用目标取模板并构造 URL */
export function buildActiveStockExternalUrl(symbol: string): string | null {
  const { target, templates } = loadStockExternalConfig()
  return buildStockExternalUrl(templates[target], symbol)
}
