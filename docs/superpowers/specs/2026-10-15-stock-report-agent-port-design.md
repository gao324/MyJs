# 个股深度研报 agent 原生移植（替换默认分析引擎）设计 v1

- 日期：2026-10-15
- 状态：待用户评审
- 参考实现：`C:\Users\19229\Desktop\stock_ai`（`report_pipeline.py` 七分片流水线，README 称 v4 模板流水线）
- 定位（2026-10-15 用户确认）：**替换本仓库「个股 AI 分析」的默认引擎**；`dsa` / `legacy` 引擎保留为一键回退，不删除
- 一致性要求（2026-10-15 用户确认）：输出与 stock_ai **一模一样**——数据全同源（§2.2）、渲染与终验原样移植（§2.4）、模型配置指向同款端点（§2.3）；LLM 文字层为同分布采样（§6 边界说明）
- 回退基线：移植前检查点提交 `21bf386`（2026-10-15，收录 DSA 接入等未提交工作区）；`git reset --hard 21bf386` 可整体回退

## 1. 目标

把 stock_ai 的个股深度研报 agent 原生移植进本仓库，成为「个股 AI 分析」默认引擎（`STOCK_ANALYSIS_ENGINE=report`）：

```
触发（现有分析对话框）→ Phase 1 数据收集（能力路由 + 日缓存）
  → Phase 2 七分片 LLM（ai_provider + 研报专用配置）
  → Phase 3 模板渲染 → Phase 4 终验（17 条全绿才发布）
  → data/user_data/stock_reports/stock-report-{id}.html（新标签页打开）
```

产出与 stock_ai 一模一样：交互式 HTML 深度研报（76 槽位模板、ECharts、双主题）——数据、模板、渲染、终验全同源同款；分析文字为同模型同分布采样（边界见 §6）。闲时 3–8 分钟/份。

## 2. 方案要点

### 2.1 引擎接入（替换默认 + 回退开关）

- `config.py`：`stock_analysis_engine` 默认值 `dsa` → `report`；合法值 `report | dsa | legacy`，非法值 fail-closed（现有 `resolve_engine` 模式扩展）。
- `stock_analyzer.py`：分发器新增 `report` 分支 → 新模块 `stock_report_pipeline.analyze_stock_stream()`；`dsa`/`legacy` 分支零改动。
- **NDJSON 协议不变**（`meta / delta / error / done`），与 DSA 接入同一契约：
  - `meta`：`{symbol, summary}`（报告引擎无关键价位，`levels` 缺省）；
  - `delta`：进度文本（Markdown），Phase 1 逐块/Phase 2 分片 x/7/重试提示；
  - `done`：新增可选字段 `report_id` / `report_url`（旧引擎不带，前端向后兼容）；
  - `error`：fail-closed，消息含原因与回退方式（同 DSA 模式）。
- 沿用 `api/stock_analysis.py` 的 `with_heartbeat` 流式包装（3–8 分钟长连接保活）。

### 2.2 数据层：全量 provider 化（工作量大头）

**新增 4 个域能力**（`CAPABILITY_REGISTRY` + preferences getter + capability-matrix 注入 + 矩阵测试，CONTRIBUTING §4 既有模式）：

| 能力 id | 覆盖块（akshare 接口） |
| --- | --- |
| `research` | 券商研报 `stock_research_report_em`、机构评级 `stock_institute_recommend`、基金持仓 `stock_report_fund_hold_detail`（盈利预测取自研报行内字段） |
| `shareholders` | 十大股东/流通股东 `stock_gdfx_top_10_em` / `stock_gdfx_free_top_10_em`、股东户数 `stock_zh_a_gdhs_detail_em`、股本结构 `stock_zh_a_gbjg_em`、限售解禁 `stock_restricted_release_queue_em`、高管持股变动 `stock_share_hold_change_sse/szse` |
| `fund_flow` | 个股资金流向 `stock_individual_fund_flow`、融资融券 `stock_margin_detail_sse/szse`、龙虎榜 `stock_lhb_detail_em`（不复用 fuyao 专有 dragon_tiger，避免跨源耦合） |
| `company_info` | 公司概况 `stock_individual_basic_info_xq`、主营构成 `stock_zygc_em`、个股新闻 `stock_news_em`、公告 `stock_notice_report`、业绩预告/快报 `stock_yjyg_em` / `stock_yjkb_em`、分红送配 `stock_history_dividend_detail` |

- 新插件 `backend/app/plugins/akshare_ext/`：`plugin.yaml`（`runtime: python`、`datasets: [research, shareholders, fund_flow, company_info]`、`install_hint: pip install akshare`）+ `provider.py`（每数据集一个 `get_*` 方法，返回 Polars DataFrame）；`availability()` 检测 `import akshare`。
- 命名不用裸 `akshare`：本插件只提供研报扩展数据集，不声明 daily/realtime 等基础数据集，避免被误解为 TickFlow 全量替代源。
- TickFlow 不提供这 4 类数据 → 矩阵 candidates 不含 TickFlow，`default` 指向本插件；akshare 未安装时 pending + 置灰（矩阵既有语义，`tf_tier` 对新能力不适用，矩阵测试覆盖该分支）。
- **同源保真块（用户要求输出与 stock_ai 一模一样）**：日 K（`stock_zh_a_daily`）、财务摘要/三大报表（`stock_financial_abstract` / `stock_financial_abstract_ths` / `stock_financial_report_sina`）、实时快照（`stock_zh_a_spot` 过滤单只）**同样从 akshare_ext 取**：插件额外提供非路由方法（如 `get_report_kline` / `get_report_financials` / `get_report_spot`），不声明 daily/financial/realtime 数据集、不进能力矩阵、不改变全局路由；研报管线经 provider 直调锁定与 stock_ai Phase 1 完全同源（非路由直调先例：dragon_tiger 直接经 custom_sources 调 fuyao provider）。**不复用**面板 DataStore / financial 路由 / quote_service——GM 源与 akshare 存在数值口径差异，会破坏一致性。块映射在 `stock_report_pipeline.build_blocks()` 收口。
- 契约红线（plugin-development.md）：symbol 带交易所后缀；单位显式转换（不启发式）；缺字段返回 None；结构漂移打告警不静默；空数据不抛异常。新数据集契约由本仓库定义，字段口径与 digest 消费直接对齐——数值与 akshare 原值一致，仅做确定性单位/symbol 标准化。
- 日缓存：`data/report_cache/{symbol}/{yyyymmdd}.json`（整包 30 块落盘，dragon_tiger 同款按日落盘模式），同日重复生成直接复用；块缺失标 `数据缺失` 进 digest，报告降级（保留 stock_ai 填充率 < 50% 判降级语义）。
- MVP 不采集 1 分钟分时块（stock_ai 采集但 digest/渲染均不消费）。

### 2.3 LLM 管线

- 新模块 `services/report_shards.py`：移植 `analyze_api.py` 的 `build_digest`、七分片 system/user prompt（`content_schema` 骨架机械生成）、JSON 抽取、分片校验/收敛式重试/降级、`fill_rate`——逻辑原样，仅数据来源与 LLM 客户端替换。
- LLM 调用走 `ai_provider.generate_ai_text`（chat_completions 协议）。**小扩展**：ai_provider 增加可选 `response_format` 参数（仅 openai_compat 协议透传；上游 400 拒绝时自动去参重发一次——沿用 stock_ai 的 `JSON_MODE_DISABLED` 回退语义）。
- 新配置（`config.py` + secrets_store，逐项缺省回退全局 `AI_*`）：`REPORT_AI_BASE_URL` / `REPORT_AI_MODEL` / `REPORT_AI_API_KEY` / `REPORT_AI_MAX_OUTPUT_TOKENS`。
- 与 stock_ai 同风格：`REPORT_AI_*` 建议指向与 stock_ai `.env` 相同的端点与模型（ARK `deepseek-v4.1-flash`）；`REPORT_AI_MAX_OUTPUT_TOKENS` 默认 ≥ 12000（七分片上限表最大值，避免输出被 ai_provider 的帽钳制）。
- 保留 stock_ai 参数：temperature 0.2、七分片 max_tokens 上限表（6000–12000）、每片最多 3 次校验重试、身份字段强制回填。
- 单份报告 token 量级（stock_ai 600519 实测）：prompt ≈ 65K 字符（≈4 万 token）/completion ≈ 16K 字符（≈1 万 token）。

### 2.4 渲染、终验与存储

- 新包 `backend/app/report_render/`：移植 `render_report.py`、`render_report_loops.py`、`content_schema.py`、`validate_html.py`、`technical_indicators.py`；`shared/` 三模板（template_report.html / template_base.css / template_base.js）作为资产文件随包（`JS_TEMPLATE` 等相对路径改为包内资源）。
- 指标一致：技术指标/信号块由移植的 `technical_indicators.py` 在**同源 akshare 日 K** 上本地计算（stock_ai 同款算法参数），不从面板 enriched 取——指标数值与 stock_ai 完全一致，无列名映射风险。
- HTML 落盘 `data/user_data/stock_reports/stock-report-{id}.html`；元数据（id/symbol/name/状态/各阶段耗时/分片失败清单/填充率/终验结果）走 `JsonReportStore` 模式（`stock_reports.json`，保留最近 50 份，超限裁剪时同步删 HTML 文件）。
- 终验 17 条全绿才 draft→final 原子发布（`os.replace`）；终验失败任务标 failed、草稿保留备查。

### 2.5 API 与任务编排

- `POST /api/stock-reports/generate {symbol}`：同 symbol 进行中 → 返回已有任务 id（防重）；并发受 `heavy_job_limiter` 约束；`task_registry` 的 `TASK_KINDS` 增加 `stock_report`（跨设备接回）。
- `GET /api/stock-reports/{id}`：状态+进度（阶段/分片 x/7/错误/降级项）。
- `GET /api/stock-reports/{id}/html`：serve HTML（`Content-Type: text/html`）。
- `GET /api/stock-reports?symbol=`：历史列表（MVP 只服务完成态「打开报告」，列表 UI 留后续）。
- 后台执行：asyncio 任务内串行执行 Phase 1→4（stock_ai 同款串行分片避免限流）；进度经内存任务句柄推送 NDJSON。

### 2.6 前端

- `StockAnalysisDialog` + `custom/stock-analysis/extension.tsx`：等待态文案改「深度研报生成中，通常 3–8 分钟」；`done` 事件带 `report_id/report_url` 时完成态渲染「打开报告」按钮（新标签页）；降级报告同步显示降级提示。
- `lib/api.ts` 仅扩展流事件类型（`done` 的 `report_id/report_url/degraded`）；对话框流已覆盖进度与完成态，不新增请求函数与查询键（YAGNI）。
- 4 个新能力均不可用（akshare 未装/插件未启用）时，后端 fail-closed 返回明确错误（含安装与启用指引），对话框走 error 事件展示；入口按钮置灰 + tooltip 门控 UI 留后续迭代（矩阵 `usable` 口径已就绪）。
- `REPORT_AI_API_KEY` 与全局 `AI_API_KEY` 均未解析到时 fail-closed 报错（逐项回退语义见 §2.3）。
- `pnpm build` 验证；覆盖加载/生成中/完成/失败四态（禁用态随门控 UI 后续补）。

## 3. 非目标

- `update_stock_report.py` 增量更新（K 线/新闻刷新）——留后续迭代。
- 独立研报中心页面（历史列表 UI）；mcp-server 侧工具；`llm_pipeline.py` 实验路径（README 已标注被 v4 取代）。
- 不修改 `dsa` / `legacy` 引擎本体；不动 TickFlow 既有能力路由（新能力不抢）。
- 不把 akshare 加入 backend 基础依赖（插件可选，`availability` 检测 + pending 降级；DSA spec §2.3 同款取舍）。

## 4. 工作量分解（合计 ≈ 6.5–8.5 人日）

| 模块 | 内容 | 工作量 |
| --- | --- | --- |
| 能力注册 | 4 域能力 + preferences getter + 矩阵注入 + 矩阵测试 | 0.5 天 |
| akshare_ext 插件 | ~20 接口封装（含同源保真 5 接口）+ 字段标准化 + 契约测试 | 2–2.5 天 |
| 块收集服务 | DataStore/financial/quote 复用 + 日缓存 + 块映射 | 0.5–1 天 |
| LLM 管线 | report_shards 移植 + ai_provider response_format 扩展 + REPORT_AI_* 配置 | 1 天 |
| 渲染终验存储 | report_render 移植 + 指标对齐 + JsonReportStore | 0.5–1 天 |
| API 任务编排 | 4 个端点 + 防重 + task_registry + heavy_job_limiter | 0.5–1 天 |
| 引擎接入前端 | 分发器 + 对话框文案/完成态 + 五态覆盖 | 0.5 天 |
| 测试文档 | 补齐定向测试 + .env.example / README / docs 更新 | 1 天 |

## 5. 验证（按 CONTRIBUTING §9 + secondary-development §7）

1. 引擎分发：`report`/`dsa`/`legacy` 各自分发单测；非法值产出 error 事件（扩展现有 `test_stock_analyzer_engine_switch.py`）。
2. provider 契约测试：4 数据集 ×（字段标准化/空数据/缺依赖 fail-closed/结构漂移告警）；能力矩阵测试：新能力候选不含 TickFlow、pending/usable 两态。
3. report_shards 单测：digest 缺块标「数据缺失」、JSON 校验失败收敛式重试、身份字段强制回填、填充率含整块缺席封顶 0.4。
4. pipeline/API 测试：成功路径、分片降级仍产出（状态 degraded）、终验失败不发布、同 symbol 防重、列表裁剪同步删文件。
5. 前端：`pnpm build` + 五态（加载/生成中/完成/失败/禁用）手工核对。
6. `uv run --frozen ruff check`（改动文件）+ `git diff --check` + `git status` 复核最终 diff。
7. 真模型端到端（用户单独确认后执行）：对一只股票全流程跑一份，17 条终验全绿；建议先用 stock_ai 验证过的 ARK `deepseek-v4.1-flash`。
8. **一致性验收（「一模一样」的可证伪口径）**：同一股票（建议 600519，stock_ai 已有当日基线数据）+ 相同模型配置，stock_ai 与本管线各生成一份：
   - 数据层：本管线 `data/report_cache/{symbol}/{yyyymmdd}.json` 与 stock_ai `output/data_{code}.json` 同日对比，经确定性单位/symbol 标准化后块级数值应一致；
   - 结构层：content 15 块 schema 键齐全、17 条终验全绿、报告章节/槽位完整；
   - 文字层：LLM 文字为同分布采样，人工抽检风格、结论方向与数据引用一致（不要求逐字相同）。

## 6. 风险与回滚

- **akshare 上游无契约、字段漂移**：provider 内字段容错 + 显式告警；接口整体变化时块缺失降级而非崩溃。
- **ai_provider JSON mode 兼容性**：response_format 透传遇 400 自动去参重发；JSON 模式失效靠七分片校验重试兜底（stock_ai 已验证该回退可行）。
- **模型遵从度**：REPORT_AI_* 未配置时回退全局配置，若全局模型 JSON 遵从度差会导致重试增多/分片降级——文档中提示推荐模型。
- **「一模一样」的边界**：数据/模板/渲染/终验等确定性部分可逐项一致（§5 第 8 条验收口径）；LLM 生成文字受采样随机性影响——stock_ai 自身对同一股票重跑两次，报告文字也不逐字相同。
- **长任务资源**：3–8 分钟任务受 heavy_job_limiter 约束；服务重启时 running 任务孤儿化（task_registry 24h 孤儿回收既有语义）。
- **回滚**：`.env` 设 `STOCK_ANALYSIS_ENGINE=dsa`（或 `legacy`）即回旧引擎；彻底回滚 `git reset --hard 21bf386`。
