// @vitest-environment node
/**
 * 策略结果列表默认列 — 股息率 (内置运行时列) 与所属行业/所属概念 (扩展数据默认列)。
 *
 * 口径约束:
 * - 股息率列默认可见, 紧跟涨跌幅 (数据由后端 TTM 运行时列提供, 小数制);
 * - 行业/概念列不硬编码扩展表 id/字段名, 按扩展 schema 里的维度字段动态生成,
 *   没有对应扩展表时不出现 (否则是一片永远空白的列);
 * - 旧列配置 (没有新列) 合并后应补上新默认列, 且不破坏用户已有顺序/显隐。
 */
import { describe, expect, it } from 'vitest'

const CONCEPT_FIELD = { name: '所属概念', label: '所属概念', type: 'string' }
const INDUSTRY_FIELD = { name: '所属同花顺行业', label: '所属同花顺行业', type: 'string' }

const SCHEMA = [
  {
    id: 'ext_hy_ths',
    label: '扩展行业',
    columns: [
      { name: 'symbol', label: '标的代码', type: 'string' },
      { name: '行业热度', label: '行业热度', type: 'float' }, // 数字字段不是维度
      INDUSTRY_FIELD,
    ],
  },
  {
    id: 'ext_gn_ths',
    label: '扩展概念',
    columns: [
      { name: 'symbol', label: '标的代码', type: 'string' },
      CONCEPT_FIELD,
      { name: '所属概念2', label: '备用概念', type: 'string' }, // 概念只要第一个匹配
    ],
  },
]

/** 旧版保存的列配置: 只有内置列, 没有股息率/行业/概念 */
const SAVED_COLUMNS = [
  { id: 'builtin:strategies', source: { type: 'builtin', key: 'strategies' }, label: '策略', visible: false, align: 'left' },
] as ColumnConfig[]

import {
  SCREENER_BUILTIN_COLUMNS,
  composeScreenerColumns,
  defaultDimensionExtColumns,
  withDefaultExtColumns,
  DIVIDEND_YIELD_HINT,
  DIVIDEND_YIELD_TTM_HINT,
  type ColumnConfig,
} from './screener-columns'

function builtin(key: string) {
  return SCREENER_BUILTIN_COLUMNS.find(c => c.source.type === 'builtin' && c.source.key === key)
}

describe('SCREENER_BUILTIN_COLUMNS: 股息率两列 (GM 口径)', () => {
  it('主列「股息率」默认可见、右对齐, 紧跟涨跌幅', () => {
    const ids = SCREENER_BUILTIN_COLUMNS.map(c => c.id)
    expect(ids.indexOf('builtin:dividend_yield')).toBe(ids.indexOf('builtin:pct') + 1)
    expect(builtin('dividend_yield')).toMatchObject({
      label: '股息率',
      visible: true,
      align: 'right',
    })
  })

  it('备用列「股息率TTM」存在且默认隐藏', () => {
    expect(builtin('dividend_yield_ttm')).toMatchObject({
      label: '股息率TTM',
      visible: false,
      align: 'right',
    })
  })

  it('口径说明点明 GM 数据源与窗口 (LFY / TTM)', () => {
    expect(DIVIDEND_YIELD_HINT).toContain('GM')
    expect(DIVIDEND_YIELD_HINT).toContain('上一财年')
    expect(DIVIDEND_YIELD_TTM_HINT).toContain('GM')
    expect(DIVIDEND_YIELD_TTM_HINT).toContain('滚动 12 个月')
  })
})

describe('defaultDimensionExtColumns: 扩展数据维度列', () => {
  it('按维度字段识别行业/概念, 跳过数字字段, 行业在前', () => {
    const cols = defaultDimensionExtColumns(SCHEMA)

    expect(cols.map(c => c.id)).toEqual([
      'ext:ext_hy_ths:所属同花顺行业',
      'ext:ext_gn_ths:所属概念',
    ])
    expect(cols.map(c => c.label)).toEqual(['所属行业', '所属概念'])
    expect(cols.every(c => c.visible && c.source.type === 'ext')).toBe(true)
    expect(cols[0].extDisplay).toMatchObject({ displayMode: 'tag', maxTags: 2 })
    expect(cols[1].extDisplay).toMatchObject({ displayMode: 'tag', maxTags: 3 })
  })

  it('无扩展表时返回空 (不产生永远空白的列)', () => {
    expect(defaultDimensionExtColumns([])).toEqual([])
    expect(defaultDimensionExtColumns([{ id: 'x', label: 'x', columns: [{ name: '热度', label: '热度', type: 'float' }] }])).toEqual([])
  })

  it('字段类型明确是数字时不当作维度字段', () => {
    const cols = defaultDimensionExtColumns([
      { id: 't', label: 't', columns: [{ name: '概念热度', label: '概念热度', type: 'float' }] },
    ])
    expect(cols).toEqual([])
  })
})

describe('withDefaultExtColumns', () => {
  it('插在股息率之后, 不改动入参', () => {
    const ext = defaultDimensionExtColumns(SCHEMA)
    const merged = withDefaultExtColumns(SCREENER_BUILTIN_COLUMNS, ext)

    expect(merged[merged.findIndex(c => c.id === 'builtin:dividend_yield') + 1].id)
      .toBe('ext:ext_hy_ths:所属同花顺行业')
    expect(merged[merged.findIndex(c => c.id === 'builtin:dividend_yield') + 2].id)
      .toBe('ext:ext_gn_ths:所属概念')
    expect(SCREENER_BUILTIN_COLUMNS.some(c => c.source.type === 'ext')).toBe(false)
  })

  it('没有扩展列时返回基础列副本', () => {
    const merged = withDefaultExtColumns(SCREENER_BUILTIN_COLUMNS, [])
    expect(merged).toEqual(SCREENER_BUILTIN_COLUMNS)
    expect(merged).not.toBe(SCREENER_BUILTIN_COLUMNS)
  })
})

describe('composeScreenerColumns: 旧配置补齐新默认列', () => {
  it('已保存列保留原显隐, 新默认列 (股息率/行业/概念) 各补一次', () => {
    const merged = composeScreenerColumns(SAVED_COLUMNS, defaultDimensionExtColumns(SCHEMA))
    const ids = merged.map(c => c.id)

    expect(ids.filter(id => id === 'builtin:dividend_yield')).toHaveLength(1)
    expect(ids.filter(id => id === 'ext:ext_hy_ths:所属同花顺行业')).toHaveLength(1)
    expect(ids.filter(id => id === 'ext:ext_gn_ths:所属概念')).toHaveLength(1)
    // 用户已有的显隐配置不被默认值覆盖
    expect(merged.find(c => c.id === 'builtin:strategies')?.visible).toBe(false)
  })

  it('已保存列里的新列 (含隐藏态) 不被重置, 也不重复', () => {
    const hiddenConcept = {
      ...defaultDimensionExtColumns(SCHEMA)[1],
      visible: false,
    }
    const merged = composeScreenerColumns(
      [...SAVED_COLUMNS, hiddenConcept],
      defaultDimensionExtColumns(SCHEMA),
    )

    expect(merged.filter(c => c.id === 'ext:ext_gn_ths:所属概念')).toHaveLength(1)
    expect(merged.find(c => c.id === 'ext:ext_gn_ths:所属概念')?.visible).toBe(false)
  })

  it('无已保存配置时返回默认列 (股息率后紧跟行业/概念)', () => {
    const merged = composeScreenerColumns(null, defaultDimensionExtColumns(SCHEMA))
    const ids = merged.map(c => c.id)

    expect(ids[ids.indexOf('builtin:dividend_yield') + 1]).toBe('ext:ext_hy_ths:所属同花顺行业')
    expect(ids[ids.indexOf('builtin:dividend_yield') + 2]).toBe('ext:ext_gn_ths:所属概念')
  })
})
