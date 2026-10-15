// 测试夹具: 确定性合成日线, 供公式层与图表映射层测试共用。
// 正弦波动 + 缓慢抬升, 覆盖均线上穿/下穿与涨跌交替, 保证信号与图标条件被真实触发。
import type { TdxBar } from './zhengyunJuedi'

export function syntheticBars(n = 120): TdxBar[] {
  const bars: TdxBar[] = []
  let prevClose = 10
  for (let i = 0; i < n; i++) {
    const close = 10 + i * 0.02 + Math.sin(i / 7) * 0.6 + Math.sin(i / 23) * 1.2
    const open = prevClose
    const high = Math.max(open, close) + 0.15
    const low = Math.min(open, close) - 0.15
    bars.push({ date: `D${i}`, open, high, low, close, volume: 1000 + (i % 7) * 137 })
    prevClose = close
  }
  return bars
}
