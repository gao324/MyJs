# 通达信副图指标「郑云绝底」接入日 K 副图 — 设计文档

- 日期：2026-10-14
- 状态：待评审
- 分级：L3（最小化直接修改核心源码；纯前端，无后端 / API / 数据契约改动）
- 关联规范：`CONTRIBUTING.md` §7（前端改动要求）、§9（验证矩阵）；`docs/secondary-development.md` §5（直接修改源码）

## 1. 背景与目标

策略页（Screener）点击股票打开个股详情弹窗（`StockPreviewDialog` → `StockPanel` → `StockDailyKChart` → `EChartsCandlestick`）。当前副图体系为静态注册的 `SUB_CHARTS`（成交量 / MACD / RSI / KDJ）+ 主图叠加 BOLL，指标值来自后端 enriched 行字段。

需求：在日 K 副图直接展示通达信副图公式「郑云绝底」的完整输出——线条、红绿 `STICKLINE` 柱、买卖信号、`DRAWICON` 图标与 10/80 参考线，观感对齐通达信。

目标：

1. 所有日 K 图表的副图胶囊行新增「绝底」开关（默认关闭），点击开启；
2. 公式按通达信语义在前端计算，无需后端参与；
3. 沉淀可复用的通达信序列函数原语库（`frontend/src/lib/tdx/`），后续公式只新增翻译模块。

## 2. 已确认的决策记录

| 决策点 | 结论 |
| --- | --- |
| 功能范围 | 原语库 + 公式翻译层（不做公式文本解析器、不做公式管理 UI） |
| 渲染保真度 | 全部图形：线条 + STICKLINE 柱 + 信号柱 + DRAWICON 图标 + 10/80 参考线 |
| 展示位置 | 所有日 K 图表（图表组件全站共享，胶囊自动出现在各页面） |

## 3. 公式口径分析（逐行依赖追踪）

原始公式见需求原文（郑云绝底组合指标）。以下口径为实现的唯一权威依据。

### 3.1 死代码与依赖剪枝

对每条输出语句追踪依赖后，以下中间变量**未被任何画线 / 信号 / STICKLINE / DRAWICON 输出引用**：

```text
VAR1~VAR9（含 VAR2:=SUMBARS(VOL,CAPITAL)）、VAR10、VAR12~VAR15、Q7、Q9、Q11
```

推论与收益：

- 公式中唯一依赖 `CAPITAL`（流通股本）的 `VAR2` 只喂死变量 → **剪枝后展示值完全不依赖 `stock_info.float_shares`**；
- 因此不涉及 volume（手）/ float_shares（股）的单位换算，ETF、指数等无股本标的同样可用该副图；
- 翻译层按活跃子图实现（见 4.2），死代码以注释标注「未引用，不实现」；`SUMBARS` 等原语仍在原语库中完整实现并测试，为后续公式铺路。

### 3.2 活跃依赖图（输出 ← 关键变量）

```text
STICKLINE红/绿 ← VAR17, VAR18 ← VAR11(2C+H+L)/4, VAR13=LLV(LOW,34), VAR16=HHV(HIGH,34)
AA   ← Q3=SMA(Q2,9,1), Q4=SMA(Q3,3,1) ← Q2=(C-LLV(LOW,18))/(HHV(HIGH,18)-LLV(LOW,18))*100
A1~A6, A ← EMA 链（起点 Q3）
关注 ← CROSS(AA,20), Q10 ← REF(OPEN,Q8), Q8=HHVBARS(HIGH,50)
极品底 ← MA(C,5), MA(C,10)
买   ← CROSS(AA,VAR18), REF(AA,1)
卖   ← CROSS(VAR17,AA), REF(AA,1)
绝底/郑云绝底1/2 ← V1~V4, MA(C,40/60/10), V/REF(V,1), BIAS1/2/3
DRAWICON×3 ← AA, A1, A5, Q5=AA>REF(AA,1), Q1（落点与条件见 4.4 映射表）
参考线 10、80
```

活跃变量的最大回看窗口为 60 根（`MA(C,60)`）；递归类（`SMA`/`EMA`）在默认「半年」预设（约 120 根）内充分收敛。**窗口内计算即可，不额外拉取更长历史**；窗口短于暖期（新股、区间过短）时头部输出为 `null`、信号条件按不满足处理（优雅降级）。

### 3.3 通达信方言与字面语义

| 语句 | 实现口径 |
| --- | --- |
| `绝底:=V4 AND (V1 OR V2) AND C<MA(C,10)/1.1 AND V/REF(V,1)<1 AND "BIAS.BIAS1"<-20 OR "BIAS.BIAS2"<-20 OR "BIAS.BIAS3"<-20` | TDX 中 `AND` 优先级高于 `OR`，按 `(V4 AND (V1 OR V2) AND … AND BIAS1<-20) OR (BIAS2<-20) OR (BIAS3<-20)` 字面求值（即 BIAS2/BIAS3 任一 <-20 单独即可成立）。不做「作者本意」修正 |
| `买:IF(CROSS(AA,VAR18) AND 18>REF(AA,1),10,0)` | `18>REF(AA,1)` 按字面：常数 18 大于昨日 AA。不做修正 |
| `"BIAS.BIAS1/2/3"` | 前端按通达信标准 BIAS 指标口径自算：`BIASn = (C-MA(C,N))/MA(C,N)*100`，N=6/12/24（通达信 BIAS 默认参数），N 为假设值，对拍后可调 |
| `SMA(X,N,M)` | 通达信递归加权：`Y[i] = (M*X[i] + (N-M)*Y[i-1]) / N`，**不是**简单移动平均。与后端 KDJ（`ewm alpha=1/3` 对应 `SMA(X,3,1)`）同族口径 |
| `EMA(X,N)` | `α=2/(N+1)` 标准指数加权，从首个有效值起算 |
| `SUMBARS(X,A)` | 从当前根向前累计 X，首次达到 ≥A 时返回已用根数；累计到数据头仍不达时返回已用根数 |
| `CROSS(A,B)` | `A[i]>B[i] AND A[i-1]<=B[i-1]` → 1，否则 0；任一侧空 → 0 |
| `IF(cond,a,b)` | `cond>0 ? a : b`（a/b 为序列或标量） |

### 3.4 数据口径（与现有指标一致）

- 输入为 enriched **前复权** `open/high/low/close`，与现有 MACD/KDJ/RSI 同源同口径（`CONTRIBUTING.md` §3.2：技术指标统一使用前复权价，不得混用）。
- `volume`（手）仅出现在 `V/REF(V,1)` 的**自身比率**中，单位分子分母相消，无换算风险。
- 空值约定：`HHV/LLV/MA/SUM/COUNT` 等窗口函数在暖期输出 `null`；`REF` 越界输出 `null`；算术运算任一操作数为 `null` 结果为 `null`（空值穿透）。

## 4. 方案架构

### 4.1 模块与数据流

```text
klineDaily rows（既有共享缓存, 不新增请求）
  → toOHLC（既有转换）
  → SubChartDef['juedi'].buildSeries(data)
      → juediSubSeries(data)                  [系列映射: juediChart.ts]
          → computeZhengyunJuedi(data)        [公式计算: zhengyunJuedi.ts, WeakMap<OHLC[], JuediResult> 缓存]
      → ECharts series 数组（line/bar/custom/scatter）
  → EChartsCandlestick 副图 grid（既有机制自动挂载 xAxisIndex/yAxisIndex）
```

### 4.2 新增文件与接口

**`frontend/src/lib/tdx/primitives.ts`（新增）** — 通达信序列函数原语库，纯函数、可单测：

```ts
export type Series = (number | null)[]

// 窗口/引用类
ref(x, n: number | Series): Series            // 定长+变长周期
ma(x, n): Series
ema(x, n): Series                              // α=2/(N+1)
sma(x, n, m): Series                           // TDX 递归加权
hhv(x, n) / llv(x, n): Series
hhvBars(x, n) / llvBars(x, n): Series          // 距窗口极值的根数
sum(x, n: number | Series): Series             // 定长+变长周期
sumBars(x, target: number | Series): Series
count(cond, n): Series                         // 窗口内 cond>0 的根数
cross(a, b): Series                            // 上穿→1
ifSeries(cond, a, b): Series
max/min/abs/gt/ge/lt/le/and/or/not 及序列算术（操作数为 Series 或标量）
```

**`frontend/src/lib/tdx/zhengyunJuedi.ts`（新增）** — 公式翻译层，输出结构与渲染解耦：

```ts
export interface JuediIcon { index: number; y: number; kind: 1 | 5 | 7 }
export interface TdxBar { date: string; open: number; high: number; low: number; close: number; volume?: number }
export interface JuediResult {
  aa, a1, a2, a3, a4, a5, a6, a: Series        // 线
  var17, var18: Series                         // STICKLINE 柱
  guanzhu: Series                              // 关注 0/10
  jipindi: Series                              // 极品底 0/5
  mai: Series                                  // 买 0/10
  maiY: Series                                 // 卖 85/90
  juedi1: Series                               // 郑云绝底 0/-5
  juedi2: Series                               // 郑云绝底2 0/-3
  icons: JuediIcon[]                           // DRAWICON 落点（条件满足的索引）
  refLines: [10, 80]
}
export function computeZhengyunJuedi(bars: readonly TdxBar[]): JuediResult
```

**`frontend/src/lib/tdx/juediChart.ts`（新增）** — 副图 series 与悬停信息栏映射，只产出 ECharts option 对象、不 import echarts（与图表运行时解耦，可直接单测）：

```ts
export function juediSubSeries(bars: readonly TdxBar[]): any[]   // 20 条 series: 参考线/趋势柱/线条/信号柱/图标
export function juediSubInfo(bars: readonly TdxBar[] | undefined, index: number): SubInfoItem[]
```

`TdxBar` 与图表 `OHLC` 结构兼容（`OHLC[]` 可直接传入），工具层不反向依赖组件层。

翻译层逐行对应原公式命名（`var11/var13/…`、`q1/q2/…`），活跃子图全量实现；死代码（3.1）以注释标注不实现。结果用 `WeakMap<OHLC[], JuediResult>` 缓存——数据引用不变时（dataZoom、悬停等触发的 option 重建）零重算；K 线数据刷新（含 SSE 实时失效）天然换引用、自动重算，无需手动失效。

### 4.3 核心文件最小接线（`frontend/src/components/EChartsCandlestick.tsx`）

1. `SUB_CHARTS` 数组**追加**一个 `SubChartDef`：`key: 'juedi'`、`label: '绝底'`、`height: 100`、`buildSeries`（调翻译层并映射 series）、`buildInfo`（悬停信息栏，见 4.4）。不改任何既有 def 与布局逻辑；胶囊开关、grid 布局、高度计算由 `StockDailyKChart` 既有机制自动获得。
2. `SubChartDef` 新增可选 `renderOrder?: number`（副图纵向顺序，默认 0，数值小者靠上；**不影响胶囊行顺序**），并抽出 `orderedSubChartDefs(activeIndicators)` 作为副图纵向顺序的唯一权威。`juedi` 取 `renderOrder: -1`，因此激活后紧贴主图、成交量等常规副图落到其下方（与通达信「附图紧贴主图」一致）。grid 装配、信息栏 graphic 与 `StockDailyKChart` 的受控高度三处共用该函数，任一处单独改顺序都会造成标签错位或高度不符。
3. `SubChartDef.buildInfo` 签名从 `(d: OHLC | null)` 扩展为 `(d: OHLC | null, data: OHLC[], index: number)`，调用点 `buildSubInfoGraphics` 传 `data` 与 `infoIdx`（该函数作用域内已有）。追加参数向后兼容，既有四个 def 不受影响。本副图信息栏展示：`AA / A1 / A / VAR17 / VAR18 / 买 / 卖 / 关注`（取悬停根的值）。

### 4.4 渲染映射（全保真）

| 通达信语句 | ECharts 渲染 | 颜色 |
| --- | --- | --- |
| `AA` COLORYELLOW | `line` | `#FFFF00` |
| `A1`~`A6` | `line` ×6 | `#FFDDCC / #FFBBCC / #FF99CC / #FF55CC / #FF33CC / #FF11CC` |
| `A` COLORFF00CC | `line` | `#FF00CC` |
| `STICKLINE(VAR17-VAR18>0, VAR17, VAR18, 8, 1)` COLORRED | `custom` series 逐根画 VAR18→VAR17 竖柱（文件内已有 custom series 先例） | 红 `#FF0000` |
| `STICKLINE(VAR17-VAR18<0, …)` COLORGREEN | 同上 | 绿 `#00FF00` |
| `关注` COLOR00FF00 | `bar`（0 值透明不画；原文为 0↔10 折线，视觉等效为 0 基线短柱，避免 0 基线横线干扰） | `#00FF00` |
| `极品底` COLORRED | `bar`（同上） | `#FF0000` |
| `买` COLORMAGENTA LINETHICK2 | `bar` 加宽 | `#FF00FF` |
| `卖` COLORGREEN（85/90） | `line`（绿色阶梯线） | `#00FF00` |
| `郑云绝底`/`郑云绝底2` STICK | `bar`（0↔-5 / 0↔-3 向下柱） | `#FF0000` |
| `10;` `80;` | 常量 `line`（细虚线，参与 Y 轴自适应） | 中性色 |
| `DRAWICON(…,1)` CROSS(AA,A5) AND REF(AA,1)<34 → y=`REF(AA,1)` | `scatter`，符号 ▲ | 红 |
| `DRAWICON(…,5)` CROSS(A1,AA) AND REF(AA,1)>80 → y=`REF(AA,3)` | `scatter`，符号 ◆ | `#CFB53B` |
| `DRAWICON(…,7)` (CROSS(AA,A1) AND Q5 AND A1<20 AND Q1>9) OR (A1>REF(A1,1) AND Q1>4 AND A1<60) → y=`30` | `scatter`，符号 ● | `#FFFFCC` |

Y 轴：不设固定区间（同 MACD，`scale: true` 随 dataZoom 自适应；10/80 参考线作为数据序列参与定界）。

公式字面色值（通达信调色板）直接使用；图标用网页风格几何符号替代通达信内置位图（形态不同、语义一致）。

## 5. 缓存与状态影响

- 不新增请求、不新增查询键：数据复用既有 `klineDailyQueryOptions` 共享缓存。
- 公式结果缓存：模块内 `WeakMap`，随 `OHLC[]` 引用存亡，无失效逻辑负担。
- `activeIndicators` 为组件本地 state（现状即如此，默认 `['vol']`），「绝底」默认关闭、关闭时零计算。

## 6. 测试计划

| 层 | 内容 |
| --- | --- |
| `primitives.test.ts` | 手算小样本断言：递归 `SMA` 逐项值、`EMA` 起算与收敛、`HHV/LLV` 窗口边界、`HHVBARS/LLVBARS`、`CROSS`（上穿/相等/暖期/含 null）、`SUMBARS`、变长 `REF/SUM`、`COUNT`、空值穿透 |
| `zhengyunJuedi.test.ts` | 确定性合成 OHLCV（~80 根）上断言：暖期头部为 null；`卖∈{85,90}`、`关注∈{0,10}`、`极品底∈{0,5}`、`买∈{0,10}`、`郑云绝底1∈{0,-5}`、`郑云绝底2∈{0,-3}`；信号与 DRAWICON 落点按原公式条件用原语库独立重算后逐根比对；参考线存在 |
| `juediChart.test.ts` | series 映射契约：线条颜色/数据逐根一致；趋势柱按 `VAR17-VAR18` 符号染红/绿且相等不画；信号柱 0 值不画；DRAWICON 拆三个 scatter；信息栏取值 |
| `EChartsCandlestick.juedi.test.tsx` | 集成接线（jsdom + mock echarts）：激活时公式 series 挂到副图 grid（`xAxisIndex=1`）、grid 数为 2；未激活时不渲染且 grid 数为 1（已做变异检查：临时改 key 后该测试失败） |
| `barsFixture.ts` | 测试夹具（合成日线），非测试文件、不被 vitest 收集 |
| 既有测试 | 核对 `EChartsCandlestick.test.tsx` 等是否断言子图集合，受影响则同步更新 |
| 构建 | `pnpm build`（tsc + vite） |
| 手工检查 | 开/关胶囊、切股、切日K/分时、实时刷新（SSE 失效后重算）、深浅主题、空数据/短历史（新股）、窄屏、`git diff --check` |

## 7. 兼容性与影响面

- 无后端、API、数据契约、查询键改动；历史数据读取不受影响。
- `EChartsCandlestick.tsx` 属核心共享组件（不在 §5.2 高冲突热点清单）：改动为「追加 1 个 def + 1 处 buildInfo 调用点传参扩展 + 1 处 SubChartDef 接口签名扩展」，上游合并时复核点即这三处。
- 未启用「绝底」胶囊时，图表行为与现状完全一致（零计算、零渲染开销）。

## 8. 风险与未验证项

1. **无法自动对拍真实通达信输出**（本机无通达信）：函数语义按通达信公开文档定义实现并用单测锁定；上线后需与通达信截图人工比对，预期偏差集中在暖期头部、图标样式与 BIAS 参数假设（N=6/12/24），对拍后如有偏差再校准。
2. **复权口径差异**：本系统指标基于前复权价；用户本地通达信若按不复权显示，同一日数值会不同——属显示口径差异，非计算错误。
3. 窗口暖期差异：半年/一年预设下活跃变量已充分收敛；极短自定义区间（如 30 根）时部分输出为 null、信号不触发——按优雅降级接受。
4. 图标以几何符号替代通达信位图。

## 9. 明确不做的事（YAGNI）

- 不做公式文本解析器与公式管理 UI（粘贴公式自动解析属未来真实需求出现后再立项）；
- 不做后端/Polars 计算（变长周期函数天然逐根递推，向量不友好；前端 250 根规模计算 <1ms）；
- 不做分时 tab 副图；不改动 BOLL 主图叠加与既有四个副图；
- 不实现死代码变量（见 3.1）。
