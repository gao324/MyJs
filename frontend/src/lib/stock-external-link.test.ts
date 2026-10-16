// @vitest-environment jsdom
import { beforeEach, expect, it } from 'vitest'
import {
  buildActiveStockExternalUrl,
  buildStockExternalUrl,
  loadStockExternalConfig,
  saveStockExternalConfig,
} from './stock-external-link'

const LEGACY_KEY = 'stock_external_template'
const CONFIG_KEY = 'stock_external_links'

beforeEach(() => {
  localStorage.clear()
})

// ===== buildStockExternalUrl: scheme 闸门 =====

it('放行 http/https 模板并替换占位符', () => {
  expect(buildStockExternalUrl('https://x.com/q?c={code}&m={market}', '600519.SH'))
    .toBe('https://x.com/q?c=600519&m=sh')
})

it('放行 tdx scheme (通达信, 旧实现只放行 http/https 会返回 null)', () => {
  expect(buildStockExternalUrl('tdx://quote?code={code}', '600519.SH'))
    .toBe('tdx://quote?code=600519')
})

it('放行 hexin scheme (同花顺)', () => {
  expect(buildStockExternalUrl('hexin://stock?code={symbol}', '000001.SZ'))
    .toBe('hexin://stock?code=000001.SZ')
})

it('挡掉 javascript: 与 data:', () => {
  expect(buildStockExternalUrl('javascript:alert(1)', '600519.SH')).toBeNull()
  expect(buildStockExternalUrl('data:text/html,x', '600519.SH')).toBeNull()
  expect(buildStockExternalUrl('vbscript:msgbox(1)', '600519.SH')).toBeNull()
  expect(buildStockExternalUrl('blob:https://x.com/abc', '600519.SH')).toBeNull()
})

it('http 仍要求 // 授权部分 (与旧行为一致)', () => {
  expect(buildStockExternalUrl('http:600519', '600519.SH')).toBeNull()
})

it('空模板或无 scheme 的模板返回 null', () => {
  expect(buildStockExternalUrl('', '600519.SH')).toBeNull()
  expect(buildStockExternalUrl('   ', '600519.SH')).toBeNull()
  expect(buildStockExternalUrl('600519 贵州茅台', '600519.SH')).toBeNull()
})

// ===== buildStockExternalUrl: symbol 形状守卫 =====

it('只接受 6 位数字 + 沪深北后缀的 symbol', () => {
  expect(buildStockExternalUrl('tdx://q?c={code}', '600519.SH')).not.toBeNull()
  expect(buildStockExternalUrl('tdx://q?c={code}', '000001.SZ')).not.toBeNull()
  expect(buildStockExternalUrl('tdx://q?c={code}', '830799.BJ')).not.toBeNull()
  // 守卫是纯形状判断 (6 位数字 + 交易所后缀), 不做资产类型语义判别
  expect(buildStockExternalUrl('tdx://q?c={code}', '399001.SH')).not.toBeNull()
  // 位数不对 / 缺后缀 / 小写后缀 / 带市场前缀 一律拒绝
  expect(buildStockExternalUrl('tdx://q?c={code}', '600519')).toBeNull()
  expect(buildStockExternalUrl('tdx://q?c={code}', '39900.SZ')).toBeNull()
  expect(buildStockExternalUrl('tdx://q?c={code}', '3990011.SZ')).toBeNull()
  expect(buildStockExternalUrl('tdx://q?c={code}', '600519.sh')).toBeNull()
  expect(buildStockExternalUrl('tdx://q?c={code}', 'sh600519')).toBeNull()
  expect(buildStockExternalUrl('tdx://q?c={code}', '')).toBeNull()
})

it('三个占位符同时替换', () => {
  expect(buildStockExternalUrl('tdx://q?c={code}&m={market}&s={symbol}', '600519.SH'))
    .toBe('tdx://q?c=600519&m=sh&s=600519.SH')
})

// ===== 配置读写与迁移 =====

it('无任何配置时返回默认 (通达信, 两个槽都空)', () => {
  const cfg = loadStockExternalConfig()
  expect(cfg.target).toBe('tdx')
  expect(cfg.templates).toEqual({ tdx: '', hexin: '' })
})

it('旧 key 单模板迁入当前目标的槽位, 且旧 key 保留可回退', () => {
  localStorage.setItem(LEGACY_KEY, JSON.stringify('https://legacy.example/{code}'))

  const cfg = loadStockExternalConfig()
  expect(cfg.target).toBe('tdx')
  expect(cfg.templates.tdx).toBe('https://legacy.example/{code}')
  expect(cfg.templates.hexin).toBe('')
  // 非破坏性迁移: 旧 key 不删除
  expect(localStorage.getItem(LEGACY_KEY)).toBe(JSON.stringify('https://legacy.example/{code}'))
})

it('新 key 存在时不再读旧 key', () => {
  localStorage.setItem(LEGACY_KEY, JSON.stringify('https://legacy.example/{code}'))
  saveStockExternalConfig({ target: 'hexin', templates: { tdx: 'tdx://a?{code}', hexin: 'hexin://b?{code}' } })

  const cfg = loadStockExternalConfig()
  expect(cfg.target).toBe('hexin')
  expect(cfg.templates.tdx).toBe('tdx://a?{code}')
  expect(cfg.templates.hexin).toBe('hexin://b?{code}')
})

it('配置往返保存/读取', () => {
  saveStockExternalConfig({ target: 'hexin', templates: { tdx: '', hexin: 'hexin://q?{code}' } })
  expect(loadStockExternalConfig()).toEqual({
    target: 'hexin',
    templates: { tdx: '', hexin: 'hexin://q?{code}' },
  })
})

it('损坏的新 key 回退默认, 不抛异常', () => {
  localStorage.setItem(CONFIG_KEY, '{not json')
  expect(loadStockExternalConfig().target).toBe('tdx')
})

it('未知 target 值回退到 tdx', () => {
  localStorage.setItem(CONFIG_KEY, JSON.stringify({ target: 'eastmoney', templates: { tdx: 'tdx://q?{code}', hexin: '' } }))
  expect(loadStockExternalConfig().target).toBe('tdx')
})

// ===== 按启用目标取模板 =====

it('buildActiveStockExternalUrl 取的是启用目标的模板', () => {
  saveStockExternalConfig({ target: 'hexin', templates: { tdx: 'tdx://a?{code}', hexin: 'hexin://b?{code}' } })
  expect(buildActiveStockExternalUrl('600519.SH')).toBe('hexin://b?600519')

  saveStockExternalConfig({ target: 'tdx', templates: { tdx: 'tdx://a?{code}', hexin: 'hexin://b?{code}' } })
  expect(buildActiveStockExternalUrl('600519.SH')).toBe('tdx://a?600519')
})

it('启用目标的模板为空时返回 null (等于关闭外链)', () => {
  saveStockExternalConfig({ target: 'tdx', templates: { tdx: '', hexin: 'hexin://b?{code}' } })
  expect(buildActiveStockExternalUrl('600519.SH')).toBeNull()
})
