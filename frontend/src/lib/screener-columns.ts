/**
 * 策略结果列表自定义列配置。
 *
 * 内置列与分组与自选页 (watchlist-columns) 保持一致，额外保留策略特有的
 * 「策略」「评分」两列。通用列模型/合并/扩展列参数来自 list-columns。
 *
 * 「所属行业/所属概念」不作为内置列硬编码配置 id：它们来自扩展数据
 * (内置预设 ext_gn_ths / ext_hy_ths，用户可换源或自建)，默认列在加载列配置时
 * 按扩展 schema 里的维度字段动态生成 —— 没有对应扩展表就不出现空列。
 */
import { storage } from '@/lib/storage'
import { dimensionKindForSourceField, type DimensionKind } from '@/components/DimensionMembersDialog'
import {
  buildExtColumnsParam,
  mergeColumns,
  serializeColumns,
  type ColumnConfig,
  type ColumnGroup,
  type ColumnSource,
  type ExtColumnDisplayConfig,
  type CandleColumnConfig,
} from '@/lib/list-columns'

export type { ColumnConfig, ColumnGroup, ColumnSource, ExtColumnDisplayConfig, CandleColumnConfig }
export { buildExtColumnsParam, mergeColumns, serializeColumns }

/**
 * 股息率两列的口径说明 (表头悬浮 + 手机端 tooltip 共用, 避免两处文案漂移)。
 *
 * 数据源: GM 掘金估值截面 (stk_get_daily_valuation_pt), 后端每日同步到本地快照,
 * 策略结果行直接透传 GM 数值 (展示口径, 与结果 as_of 无关, 不参与回测)。
 */
export const DIVIDEND_YIELD_HINT =
  'GM 股息率 (上一财年 LFY)。GM 掘金按每股分红(上一财年) ÷ 现价计算, 数据快照为 GM 最新交易日截面。'
export const DIVIDEND_YIELD_TTM_HINT =
  'GM 股息率 (滚动 12 个月 TTM)。GM 掘金按近 12 个月分红 ÷ 现价计算, 数据快照为 GM 最新交易日截面。'

export const SCREENER_BUILTIN_COLUMNS: ColumnConfig[] = [
  // 固定列
  { id: 'builtin:symbol', source: { type: 'builtin', key: 'symbol' }, label: '标的', visible: true, pinned: true, align: 'left' },
  // 策略特有列
  { id: 'builtin:strategies', source: { type: 'builtin', key: 'strategies' }, label: '策略', visible: true, align: 'left' },
  { id: 'builtin:score', source: { type: 'builtin', key: 'score' }, label: '评分', visible: true, align: 'right' },
  // 价格
  { id: 'builtin:price', source: { type: 'builtin', key: 'price' }, label: '现价', visible: true, align: 'right' },
  { id: 'builtin:pct', source: { type: 'builtin', key: 'pct' }, label: '涨跌幅', visible: true, align: 'right' },
  // 股息率: GM dy_lfy (上一财年), 小数制 → 展示百分数; 快照无该标的为 null 显示「—」
  { id: 'builtin:dividend_yield', source: { type: 'builtin', key: 'dividend_yield' }, label: '股息率', visible: true, align: 'right' },
  // 股息率TTM: GM dy_ttm (滚动12月), 备用列默认隐藏
  { id: 'builtin:dividend_yield_ttm', source: { type: 'builtin', key: 'dividend_yield_ttm' }, label: '股息率TTM', visible: false, align: 'right' },
  { id: 'builtin:change_amount', source: { type: 'builtin', key: 'change_amount' }, label: '涨跌额', visible: false, align: 'right' },
  { id: 'builtin:amplitude', source: { type: 'builtin', key: 'amplitude' }, label: '振幅', visible: false, align: 'right' },
  // 成交
  { id: 'builtin:turnover', source: { type: 'builtin', key: 'turnover' }, label: '换手率', visible: false, align: 'right' },
  { id: 'builtin:amount', source: { type: 'builtin', key: 'amount' }, label: '成交额', visible: true, align: 'right' },
  { id: 'builtin:float_val', source: { type: 'builtin', key: 'float_val' }, label: '流通值', visible: false, align: 'right' },
  { id: 'builtin:vol_ratio', source: { type: 'builtin', key: 'vol_ratio' }, label: '量比', visible: true, align: 'right' },
  { id: 'builtin:annual_vol', source: { type: 'builtin', key: 'annual_vol' }, label: '年化波动', visible: false, align: 'right' },
  // 均线
  { id: 'builtin:ma5', source: { type: 'builtin', key: 'ma5' }, label: 'MA5', visible: false, align: 'right' },
  { id: 'builtin:ma10', source: { type: 'builtin', key: 'ma10' }, label: 'MA10', visible: false, align: 'right' },
  { id: 'builtin:ma20', source: { type: 'builtin', key: 'ma20' }, label: 'MA20', visible: false, align: 'right' },
  { id: 'builtin:ma60', source: { type: 'builtin', key: 'ma60' }, label: 'MA60', visible: false, align: 'right' },
  // 区间
  { id: 'builtin:high_60d', source: { type: 'builtin', key: 'high_60d' }, label: '60日高', visible: false, align: 'right' },
  { id: 'builtin:low_60d', source: { type: 'builtin', key: 'low_60d' }, label: '60日低', visible: false, align: 'right' },
  // 技术指标
  { id: 'builtin:rsi6', source: { type: 'builtin', key: 'rsi6' }, label: 'RSI6', visible: false, align: 'right' },
  { id: 'builtin:rsi14', source: { type: 'builtin', key: 'rsi14' }, label: 'RSI14', visible: true, align: 'right' },
  { id: 'builtin:rsi24', source: { type: 'builtin', key: 'rsi24' }, label: 'RSI24', visible: false, align: 'right' },
  { id: 'builtin:macd_dif', source: { type: 'builtin', key: 'macd_dif' }, label: 'MACD-DIF', visible: false, align: 'right' },
  { id: 'builtin:macd_dea', source: { type: 'builtin', key: 'macd_dea' }, label: 'MACD-DEA', visible: false, align: 'right' },
  { id: 'builtin:macd_hist', source: { type: 'builtin', key: 'macd_hist' }, label: 'MACD柱', visible: false, align: 'right' },
  { id: 'builtin:kdj_k', source: { type: 'builtin', key: 'kdj_k' }, label: 'KDJ-K', visible: false, align: 'right' },
  { id: 'builtin:kdj_d', source: { type: 'builtin', key: 'kdj_d' }, label: 'KDJ-D', visible: false, align: 'right' },
  { id: 'builtin:kdj_j', source: { type: 'builtin', key: 'kdj_j' }, label: 'KDJ-J', visible: false, align: 'right' },
  { id: 'builtin:boll_upper', source: { type: 'builtin', key: 'boll_upper' }, label: '布林上轨', visible: false, align: 'right' },
  { id: 'builtin:boll_lower', source: { type: 'builtin', key: 'boll_lower' }, label: '布林下轨', visible: false, align: 'right' },
  { id: 'builtin:atr14', source: { type: 'builtin', key: 'atr14' }, label: 'ATR14', visible: false, align: 'right' },
  { id: 'builtin:vol_ma5', source: { type: 'builtin', key: 'vol_ma5' }, label: '量MA5', visible: false, align: 'right' },
  { id: 'builtin:vol_ma10', source: { type: 'builtin', key: 'vol_ma10' }, label: '量MA10', visible: false, align: 'right' },
  // 动量
  { id: 'builtin:momentum_5d', source: { type: 'builtin', key: 'momentum_5d' }, label: '5D 动量', visible: false, align: 'right' },
  { id: 'builtin:momentum_10d', source: { type: 'builtin', key: 'momentum_10d' }, label: '10D 动量', visible: false, align: 'right' },
  { id: 'builtin:momentum_20d', source: { type: 'builtin', key: 'momentum_20d' }, label: '20D 动量', visible: false, align: 'right' },
  { id: 'builtin:momentum_30d', source: { type: 'builtin', key: 'momentum_30d' }, label: '30D 动量', visible: false, align: 'right' },
  { id: 'builtin:momentum_60d', source: { type: 'builtin', key: 'momentum_60d' }, label: '60D 动量', visible: true, align: 'right' },
  // 异动偏离 (交易所异动规则口径, 运行时列; 默认隐藏, 在列组「异动」中开启)
  { id: 'builtin:deviate_3d', source: { type: 'builtin', key: 'deviate_3d' }, label: '3D 偏离', visible: false, align: 'right' },
  { id: 'builtin:deviate_10d', source: { type: 'builtin', key: 'deviate_10d' }, label: '10D 偏离', visible: false, align: 'right' },
  { id: 'builtin:deviate_30d', source: { type: 'builtin', key: 'deviate_30d' }, label: '30D 偏离', visible: false, align: 'right' },
  // 连板
  { id: 'builtin:limit_ups', source: { type: 'builtin', key: 'limit_ups' }, label: '连板', visible: true, align: 'center' },
  { id: 'builtin:limit_downs', source: { type: 'builtin', key: 'limit_downs' }, label: '连跌', visible: false, align: 'center' },
  // 信号 & 图表
  { id: 'builtin:signals', source: { type: 'builtin', key: 'signals' }, label: '信号', visible: true, align: 'left' },
  { id: 'builtin:candle', source: { type: 'builtin', key: 'candle' }, label: '日k', visible: false, align: 'center' },
  { id: 'builtin:intraday', source: { type: 'builtin', key: 'intraday' }, label: '分时', visible: false, align: 'center' },
  // 财务指标 (与自选页对齐; 当前后端 enriched 未返回这些字段，默认隐藏)
  { id: 'builtin:eps', source: { type: 'builtin', key: 'eps' }, label: 'EPS', visible: false, align: 'right' },
  { id: 'builtin:bps', source: { type: 'builtin', key: 'bps' }, label: 'BPS', visible: false, align: 'right' },
  { id: 'builtin:roe', source: { type: 'builtin', key: 'roe' }, label: 'ROE', visible: false, align: 'right' },
  { id: 'builtin:pe_ttm', source: { type: 'builtin', key: 'pe_ttm' }, label: 'PE(TTM)', visible: false, align: 'right' },
  { id: 'builtin:pb', source: { type: 'builtin', key: 'pb' }, label: 'PB', visible: false, align: 'right' },
  { id: 'builtin:gross_margin', source: { type: 'builtin', key: 'gross_margin' }, label: '毛利率', visible: false, align: 'right' },
  { id: 'builtin:net_margin', source: { type: 'builtin', key: 'net_margin' }, label: '净利率', visible: false, align: 'right' },
  { id: 'builtin:revenue_yoy', source: { type: 'builtin', key: 'revenue_yoy' }, label: '营收增速', visible: false, align: 'right' },
  { id: 'builtin:net_income_yoy', source: { type: 'builtin', key: 'net_income_yoy' }, label: '净利增速', visible: false, align: 'right' },
  { id: 'builtin:debt_ratio', source: { type: 'builtin', key: 'debt_ratio' }, label: '负债率', visible: false, align: 'right' },
]

export const SCREENER_COLUMN_GROUPS: ColumnGroup[] = [
  { id: 'core', label: '核心', icon: '🎯', keys: ['strategies', 'score', 'signals'] },
  { id: 'price', label: '价格', icon: '💰', keys: ['price', 'pct', 'dividend_yield', 'dividend_yield_ttm', 'change_amount', 'amplitude'] },
  { id: 'volume', label: '成交', icon: '📊', keys: ['turnover', 'amount', 'float_val', 'vol_ratio', 'annual_vol'] },
  { id: 'ma', label: '均线', icon: '📈', keys: ['ma5', 'ma10', 'ma20', 'ma60'] },
  { id: 'range', label: '区间', icon: '📏', keys: ['high_60d', 'low_60d'] },
  { id: 'tech', label: '技术指标', icon: '🔬', keys: ['rsi6', 'rsi14', 'rsi24', 'macd_dif', 'macd_dea', 'macd_hist', 'kdj_k', 'kdj_d', 'kdj_j', 'boll_upper', 'boll_lower', 'atr14', 'vol_ma5', 'vol_ma10'] },
  { id: 'momentum', label: '动量', icon: '🚀', keys: ['momentum_5d', 'momentum_10d', 'momentum_20d', 'momentum_30d', 'momentum_60d'] },
  { id: 'abnormal', label: '异动', icon: '⚡', keys: ['deviate_3d', 'deviate_10d', 'deviate_30d'] },
  { id: 'limit', label: '连板', icon: '🔥', keys: ['limit_ups', 'limit_downs'] },
  { id: 'signal', label: '信号', icon: '📡', keys: ['signals', 'candle', 'intraday'] },
  { id: 'finance', label: '财务', icon: '📋', keys: ['eps', 'bps', 'roe', 'pe_ttm', 'pb', 'gross_margin', 'net_margin', 'revenue_yoy', 'net_income_yoy', 'debt_ratio'] },
]

export async function saveScreenerColumnConfig(columns: ColumnConfig[]): Promise<void> {
  const saveable = serializeColumns(columns)
  storage.screenerResultColumns.set(saveable)
  try {
    const { api } = await import('@/lib/api')
    await api.updateScreenerResultColumns(saveable)
  } catch {
    // 后端不可用时 localStorage 仍有效
  }
}

/** ext schema 行 (api.extDataSchemaAll 的子集, 便于纯函数测试) */
export interface ExtSchemaTable {
  id: string
  label: string
  columns: { name: string; label: string; type: string }[]
}

/** 维度字段类型判定: 已知非文本 (数字/布尔/日期) 的字段不是维度成员名。 */
function isTextLikeFieldType(type?: string): boolean {
  if (!type) return true
  return !/(int|float|double|decimal|number|bool|date|time)/i.test(type)
}

/** 维度默认列的展示配置: 概念可能十几个, 折叠到前 N 个 +N 展开 (与监控标签同款)。 */
const DIMENSION_EXT_DISPLAY: Record<DimensionKind, ExtColumnDisplayConfig> = {
  industry: { displayMode: 'tag', maxTags: 2 },
  concept: { displayMode: 'tag', maxTags: 3 },
}

const DIMENSION_LABELS: Record<DimensionKind, string> = {
  industry: '所属行业',
  concept: '所属概念',
}

// 行业在前、概念在后 (行业取值少且稳定, 概念多而长)
const DIMENSION_ORDER: DimensionKind[] = ['industry', 'concept']

/**
 * 从扩展数据 schema 生成「所属行业 / 所属概念」默认列。
 *
 * 维度识别复用 DimensionMembersDialog.dimensionKindForSourceField —— 与表格
 * 里点标签查成分股的判定同一口径, 不硬编码配置 id/字段名 (内置预设为
 * ext_hy_ths.所属同花顺行业 / ext_gn_ths.所属概念, 用户换源或自建同样命中)。
 * 缺对应扩展表/字段时该列不生成, 避免出现永远空白的列。
 */
export function defaultDimensionExtColumns(tables: ExtSchemaTable[]): ColumnConfig[] {
  const picked = new Map<DimensionKind, ColumnConfig>()
  for (const table of tables) {
    for (const field of table.columns ?? []) {
      const kind = dimensionKindForSourceField(`${table.id}.${field.name}`)
      if (!kind || picked.has(kind) || !isTextLikeFieldType(field.type)) continue
      picked.set(kind, {
        id: `ext:${table.id}:${field.name}`,
        source: {
          type: 'ext',
          configId: table.id,
          fieldName: field.name,
          fieldLabel: field.label,
          fieldType: field.type,
        },
        label: DIMENSION_LABELS[kind],
        visible: true,
        align: 'center',
        extDisplay: DIMENSION_EXT_DISPLAY[kind],
      })
    }
  }
  return DIMENSION_ORDER
    .map(kind => picked.get(kind))
    .filter((col): col is ColumnConfig => !!col)
}

/** 把默认扩展列插在「股息率」之后 (靠前的窄列区), 不修改入参。 */
export function withDefaultExtColumns(
  base: ColumnConfig[],
  extColumns: ColumnConfig[],
): ColumnConfig[] {
  const out = [...base]
  if (extColumns.length === 0) return out
  const anchor = out.findIndex(c => c.id === 'builtin:dividend_yield')
  out.splice(anchor >= 0 ? anchor + 1 : out.length, 0, ...extColumns)
  return out
}

/** 读取扩展 schema 生成默认维度列; 后端不可用/无扩展表 → [] (不注入空列)。 */
async function loadDefaultExtColumns(): Promise<ColumnConfig[]> {
  try {
    const { api } = await import('@/lib/api')
    const res = await api.extDataSchemaAll()
    return defaultDimensionExtColumns(res.items ?? [])
  } catch {
    return []
  }
}

/**
 * 合并用户已保存列与默认列 (默认列含本次扩展 schema 推出的维度列)。
 * saved 为空或不可用时直接返回默认列。
 */
export function composeScreenerColumns(
  saved: ColumnConfig[] | null | undefined,
  extColumns: ColumnConfig[],
): ColumnConfig[] {
  const defaults = withDefaultExtColumns(SCREENER_BUILTIN_COLUMNS, extColumns)
  return saved && saved.length > 0 ? mergeColumns(saved, defaults) : defaults
}

export async function loadScreenerColumnConfig(): Promise<ColumnConfig[]> {
  // 扩展 schema 与列配置并发取 (schema 只用于生成默认维度列)
  const extColumnsPromise = loadDefaultExtColumns()
  let savedColumns: ColumnConfig[] | null = null
  try {
    const { api } = await import('@/lib/api')
    const res = await api.screenerResultColumns()
    if (res.columns && res.columns.length > 0) savedColumns = res.columns
  } catch {
    // 后端不可用，继续尝试 localStorage
  }
  const extColumns = await extColumnsPromise

  if (savedColumns) {
    const merged = composeScreenerColumns(savedColumns, extColumns)
    storage.screenerResultColumns.set(serializeColumns(merged))
    return merged
  }

  return composeScreenerColumns(
    storage.screenerResultColumns.get([]) as ColumnConfig[],
    extColumns,
  )
}
