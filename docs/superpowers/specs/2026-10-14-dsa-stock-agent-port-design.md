# DSA 问股 agent 接入（个股 AI 分析，带回退开关）设计 v2

- 日期：2026-10-14
- 状态：用户已确认 v2，进入实施
- 参考实现：`C:\Users\19229\Desktop\stock`（该 checkout 的个股 AI 分析已走 DSA 问股 agent）
- 变更记录：v1（完整复制 vendor + 补齐依赖）→ v2（按用户选择：vendor 引用、最小依赖、保留回退开关、前端文案同步）

## 1. 目标

本仓库「个股 AI 分析」默认改走 **DSA 问股 agent**（`daily_stock_analysis`，以 vendor 形式存在于
`stock` checkout），同时保留原「四维单次 LLM 流式分析」作为一键回退；前端文案与等待态表现同步更新。
对外 NDJSON 契约不变：`meta / delta / error / done`。

## 2. 方案要点（v2）

### 2.1 后端：DSA 默认 + 旧四维回退开关

- `app/services/stock_analyzer_legacy.py`：现有 `stock_analyzer.py` **原样改名**，零逻辑改动。
- `app/services/stock_analyzer_dsa.py`：DSA 版分析器（价位 + meta + agent 透传），
  `_load_kline` 保留本仓库的 `cn_today()`（北京时区），不采用 stock 的 `date.today()`。
- `app/services/stock_analyzer.py`：薄分发器，`analyze_stock_stream()` 按开关转发：
  - `dsa`（默认）→ `stock_analyzer_dsa`
  - `legacy` → `stock_analyzer_legacy`
  - 非法值 / DSA 不可用（vendor 路径缺失、依赖缺失）→ 产出 `{"type":"error", ...}` 事件，
    消息包含原因与回退方式；**不静默换口径**。
- 开关：`config.py` 新增 `stock_analysis_engine: str = "dsa"`（env `STOCK_ANALYSIS_ENGINE`）。

### 2.2 vendor 引用而非复制

- `app/services/dsa_bridge.py` 的 vendor 根目录解析顺序：
  1. `settings.dsa_vendor_dir`（env `DSA_VENDOR_DIR`，本机在 `.env` 中配置真实路径）
  2. 未配置 → 抛错并提示配置方式。
- 仓库代码内**不写机器绝对路径**（`.env` 不入库，`.env.example` 只放占位说明）。
- 已知代价（用户已确认）：本仓库不自包含；Docker/打包环境看不到该路径，
  这些环境应设 `STOCK_ANALYSIS_ENGINE=legacy`。该功能当前仅在本机 dev 可用。

### 2.3 依赖只装必需子集

新增：`litellm`、`sqlalchemy`、`tenacity`、`json-repair`、`fake-useragent`、`newspaper3k`、
`lxml_html_clean`（版本约束沿用 stock `pyproject.toml`）。
不新增：`akshare`、`efinance`、`yfinance`、`pytdx`、`baostock`、`tushare`（数据源工具按缺库降级）。
若 executor 构建期因缺库失败，暂停实施并回报，由用户决定装齐或关闭对应工具。

### 2.4 前端文案与等待态

- 更新「四维分析」相关文案：`components/stock-analysis/StockAnalysisDialog.tsx`（2 处）、
  `custom/stock-analysis/extension.tsx`（3 处）、`pages/Onboarding.tsx`（2 处）、`lib/api.ts`（注释）。
- 等待态文案改为「agent 执行中，通常 1–3 分钟」（DSA `chat()` 非 token 级流式，整段返回）。
- 不改组件结构、不改 store、不改 NDJSON 协议。

## 3. 非目标

- 不复制 `vendor/dsa` 进本仓库；不引入 stock 侧其它 DSA 消费方（`telegram_bot`、`batch_ai_queue` 等）。
- 不修改 DSA 自身逻辑；不改 `api/stock_analysis.py` 的 `with_heartbeat` 包装。
- 不执行 git 提交/推送。

## 4. 验证

1. 开关两态：`dsa` 与 `legacy` 各自跑通分发单测；非法值产出 error 事件。
2. DSA executor 构建冒烟（只构建，不调模型）；缺 `DSA_VENDOR_DIR` 时明确报错。
3. 单测：`test_dsa_bridge.py`、`test_dsa_reports.py`、`test_stock_analyzer_engine_switch.py`，
   以及保留的旧测试（import 改指 `stock_analyzer_legacy`）。
4. 前端：`pnpm build`；改动文案在页面核对。
5. `ruff check` 改动文件；`git diff --check`；`git status` 复核最终 diff。
6. 真模型端到端（需用户单独确认）：对一只股票跑一次问股。

## 5. 风险与回滚

- 风险：`DSA_VENDOR_DIR` 指向的另一个 checkout 被移动/删除后功能不可用（有明确报错）；
  最小依赖下部分 DSA 工具降级；DSA 首次问股冷启动慢（最多 12 步、600s 预算，前端有心跳保活）。
- 回滚：`.env` 设 `STOCK_ANALYSIS_ENGINE=legacy` 即回到原四维实现；彻底回滚 = 删除
  `stock_analyzer_dsa.py`/`dsa_bridge.py`/`dsa_reports.py`、恢复 `stock_analyzer.py`、移除新增依赖。
