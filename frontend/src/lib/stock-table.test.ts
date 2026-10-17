// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { getSortValue } from './stock-table'
import type { ColumnConfig } from './list-columns'

function builtinCol(key: string): ColumnConfig {
  return { id: `builtin:${key}`, source: { type: 'builtin', key }, label: key, visible: false, align: 'center' }
}

describe('getSortValue: 自选加入信息列', () => {
  it('added_at 返回北京时间完整串 (与展示同口径, 字典序即时间序)', () => {
    expect(getSortValue({ added_at: '2026-09-18T20:30:00' }, builtinCol('added_at')))
      .toBe('2026-09-19 04:30:00')
  })

  it('added_at 缺失返回 null 而非空串', () => {
    // useTableSort 会先试 Number('') === 0, 返回空串会让空值排到最前
    for (const v of ['', null, undefined]) {
      expect(getSortValue({ added_at: v }, builtinCol('added_at'))).toBe(null)
    }
  })

  it('pct_since_added 透传数值 (含 0 与负值)', () => {
    expect(getSortValue({ pct_since_added: 0.1 }, builtinCol('pct_since_added'))).toBe(0.1)
    expect(getSortValue({ pct_since_added: -0.05 }, builtinCol('pct_since_added'))).toBe(-0.05)
    expect(getSortValue({ pct_since_added: 0 }, builtinCol('pct_since_added'))).toBe(0)
    expect(getSortValue({}, builtinCol('pct_since_added'))).toBe(undefined)
  })

  it('dividend_yield 透传数值 (GM 截面, 快照缺失由后端置 null)', () => {
    expect(getSortValue({ dividend_yield: 0.0366 }, builtinCol('dividend_yield'))).toBe(0.0366)
    expect(getSortValue({ dividend_yield: 0 }, builtinCol('dividend_yield'))).toBe(0)
    expect(getSortValue({ dividend_yield: null }, builtinCol('dividend_yield'))).toBe(null)
    expect(getSortValue({}, builtinCol('dividend_yield'))).toBe(undefined)
  })

  it('dividend_yield_ttm 透传数值 (备用列)', () => {
    expect(getSortValue({ dividend_yield_ttm: 0.032 }, builtinCol('dividend_yield_ttm'))).toBe(0.032)
    expect(getSortValue({}, builtinCol('dividend_yield_ttm'))).toBe(undefined)
  })
})
