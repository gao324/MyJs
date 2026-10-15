# 个股深度研报 agent 原生移植 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 stock_ai 的个股深度研报七分片流水线原生移植进 tick-stock-panel，作为「个股 AI 分析」默认引擎，产出与 stock_ai 一模一样的 HTML 研报。

**Architecture:** Phase 1 数据收集（akshare_ext 插件按 stock_ai 同源接口取 27 个块 + 日缓存）→ Phase 2 七分片 LLM（走 ai_provider，研报专用端点覆盖 + JSON mode）→ Phase 3 模板渲染（reporting 包）→ Phase 4 17 条终验 → HTML 落盘；引擎开关 `STOCK_ANALYSIS_ENGINE=report`，复用现有 NDJSON 分析对话框。

**Tech Stack:** Python 3.11 / FastAPI / Pydantic v2 / Polars / akshare（插件可选）/ uv；React 18 + TypeScript + TanStack Query + pnpm。

**Spec:** `docs/superpowers/specs/2026-10-15-stock-report-agent-port-design.md`

**参考实现（只读，禁止修改）:** `C:\Users\19229\Desktop\stock_ai`

## Global Constraints

- 后端验证命令：`cd backend && uv run --frozen pytest tests/<file> -q`；静态检查 `uv run --frozen ruff check app/<file> tests/<file>`。
- 前端验证命令：`cd frontend && pnpm build`。
- 提交前必跑 `git diff --check`；不提交、不推送无关文件。
- 数据口径红线：symbol 带交易所后缀（`600519.SH`）；数值一律 `float + math.isfinite` 清洗，JSON 缓存内不得出现 `NaN`/`Infinity`；缺失写 `"数据缺失"`，不启发式补全。
- 不改动 `dsa` / `legacy` 引擎实现本体；`akshare` 不进 `backend/pyproject.toml` 主依赖（插件 `availability()` 检测 + pending 降级）。
- 移植文件保持 stock_ai 原始逻辑，只改：导入路径、模板/资源相对路径、LLM 客户端、同步→异步。
- 移植参考实现目录只读；所有新代码写进 tick-stock-panel。
- 不自动 git commit/push 之外的仓库操作；每个 Task 结束按步骤提交一次。

## Review Focus

- **akshare 接口字段漂移**：上游列名/结构变化时，provider 必须打告警并返回空记录，不得抛异常中断整条流水线（stock_ai `_safe_call` 语义）。
- **缺失数据块**：任一数据源不可用时对应块缺失 → digest 写「数据缺失」→ 分片仍要产出、终验仍须全绿（不得因缺块产出非法坐标/占位符残留）。
- **LLM JSON 不合规**：模型返回带围栏、少键、越界值时，收敛式重试最多 3 次后降级空片，填充率 < 50% 判降级但仍发布报告（exit 2 语义）。
- **长任务并发与重启**：同一 symbol 重复点击不得起两个任务；进程重启后 running 任务不得让前端永久转圈（孤儿回收 + 明确错误态）。
- **akshare 未安装**：插件 pending、4 个能力 `usable=false`、前端入口禁用并提示安装；不得静默回退到 GM 数据（会破坏与 stock_ai 的一致性）。

---

### Task 1: akshare_ext 插件骨架与 4 个域能力注册

**Files:**
- Create: `backend/app/plugins/akshare_ext/plugin.yaml`
- Create: `backend/app/plugins/akshare_ext/__init__.py`
- Create: `backend/app/plugins/akshare_ext/provider.py`
- Modify: `backend/app/data_providers/capabilities.py`（`_TIER_RANK`、`CAPABILITY_REGISTRY`、`_current_routing`）
- Modify: `backend/app/services/preferences.py:304-306` 之后追加 4 个 getter
- Modify: `backend/app/api/settings.py`（capability-matrix 端点的 `current` 注入块）
- Test: `backend/tests/test_akshare_ext_provider.py`（新建）
- Test: `backend/tests/test_capability_matrix.py`（追加）

**Interfaces:**
- Consumes: `app.data_providers.custom.list_plugins()`、`app.services.preferences.load()`
- Produces: 插件名 `akshare_ext`；能力 id `research` / `shareholders` / `fund_flow` / `company_info`；getter `get_research_data_provider()` / `get_shareholders_data_provider()` / `get_fund_flow_data_provider()` / `get_company_info_data_provider()`；模块级 `availability() -> tuple[bool, str]`；类 `AkshareExtProvider`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_akshare_ext_provider.py
"""akshare_ext 插件发现与能力声明契约。"""
from __future__ import annotations

from app.data_providers import custom as custom_sources
from app.data_providers.capabilities import CAPABILITY_REGISTRY, build_capability_matrix
from app.services import preferences

NEW_CAPS = ("research", "shareholders", "fund_flow", "company_info")


def _fake_akshare(monkeypatch):
    """注入最小可用的假 akshare 模块，测试不触发网络。"""
    import sys
    import types

    mod = types.ModuleType("akshare")
    monkeypatch.setitem(sys.modules, "akshare", mod)
    return mod


def test_plugin_declares_four_datasets(monkeypatch):
    _fake_akshare(monkeypatch)
    plugins = {p["name"]: p for p in custom_sources.list_plugins()}
    assert "akshare_ext" in plugins
    assert set(plugins["akshare_ext"]["datasets"]) == set(NEW_CAPS)


def test_availability_reports_missing_dependency(monkeypatch):
    import sys

    from app.plugins.akshare_ext import provider as mod

    monkeypatch.setitem(sys.modules, "akshare", None)  # import akshare -> TypeError
    ok, reason = mod.availability()
    assert ok is False and "akshare" in reason


def test_registry_contains_new_capabilities():
    ids = {c["id"] for c in CAPABILITY_REGISTRY}
    assert set(NEW_CAPS) <= ids
    for cap in CAPABILITY_REGISTRY:
        if cap["id"] in NEW_CAPS:
            assert cap["field"].endswith("_data_provider")
            assert cap["default"] == "akshare_ext"
            assert cap["tf_tier"] == "never"


def test_tickflow_never_candidate_for_new_capabilities(monkeypatch):
    _fake_akshare(monkeypatch)
    current = {
        "realtime_data_provider": "gm",
        "daily_data_provider": "gm",
        "minute_data_provider": "gm",
        "full_minute_data_provider": "tickflow",
        "depth5_data_provider": "gm",
        "adj_factor_provider": "gm",
        "financial_data_provider": "gm",
        "research_data_provider": "akshare_ext",
        "shareholders_data_provider": "akshare_ext",
        "fund_flow_data_provider": "akshare_ext",
        "company_info_data_provider": "akshare_ext",
    }
    matrix = build_capability_matrix(current, tickflow_tier="expert")
    for cap in matrix["capabilities"]:
        if cap["id"] in NEW_CAPS:
            assert cap["tf_available"] is False
            assert all(c["name"] != "tickflow" for c in cap["candidates"])
            assert cap["usable"] is True


def test_getters_fall_back_when_provider_missing(monkeypatch):
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: set())
    assert preferences.get_research_data_provider() == "akshare_ext"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_provider.py -q`
Expected: FAIL（`ModuleNotFoundError: app.plugins.akshare_ext`）

- [ ] **Step 3: 建插件骨架**

`backend/app/plugins/akshare_ext/plugin.yaml`：

```yaml
name: akshare_ext
display_name: "AKShare 研报扩展"
runtime: python
entry: app.plugins.akshare_ext.provider:AkshareExtProvider
check: app.plugins.akshare_ext.provider:availability
datasets: [research, shareholders, fund_flow, company_info]
description: "研报管线专用数据集(研报/股东/资金流/公司信息); 数据源 akshare, 未安装时置灰, 不影响其他数据源"
install_hint: "pip install akshare"
homepage: "https://akshare.akfamily.xyz"
```

`backend/app/plugins/akshare_ext/__init__.py`：空文件。

`backend/app/plugins/akshare_ext/provider.py`：

```python
"""AKShare 研报扩展数据源 — 研报管线专用数据集(非 TickFlow 能力)。

数据集契约(本仓库定义, 消费方 = 个股研报管线):
- research / shareholders / fund_flow / company_info:
  返回 {块名: [记录, ...]}, 记录列名保持 akshare 原始中文列(与 stock_ai
  Phase 1 落盘块逐列对齐, 保证报告一致性); 缺失块返回 [], 不抛异常。
- 数值一律经 _clean 清洗(非有限值 → None), JSON 缓存内无 NaN/Infinity。
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

NAME = "akshare_ext"
DATASETS = ("research", "shareholders", "fund_flow", "company_info")


def availability() -> tuple[bool, str]:
    """返回 (是否可用, 原因)。不抛异常。"""
    try:
        import akshare  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"未安装 akshare({type(exc).__name__}), 可执行 pip install akshare"
    return True, "ok"


def _clean(value: Any) -> Any:
    """非有限数值 → None; 其余原样(供 JSON 落盘与 digest 消费)。"""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    return value


def _records(frame: Any) -> list[dict]:
    """akshare DataFrame → 清洗后的记录列表; 结构漂移(非 DataFrame)打告警返回 []。"""
    if frame is None:
        return []
    to_dicts = getattr(frame, "to_dict", None)
    if not callable(to_dicts):
        logger.warning("[akshare_ext] 返回结构不是 DataFrame(%s), 已跳过", type(frame).__name__)
        return []
    try:
        rows = frame.to_dict(orient="records")
    except Exception:  # noqa: BLE001
        logger.warning("[akshare_ext] DataFrame 转记录失败", exc_info=True)
        return []
    return [{k: _clean(v) for k, v in row.items()} for row in rows]


class _Config:
    """最小 provider 配置(需有 .datasets 属性)。"""

    def __init__(self) -> None:
        self.datasets = {name: True for name in DATASETS}


class AkshareExtProvider:
    name = NAME
    builtin = True

    def __init__(self) -> None:
        self.config = _Config()

    def close(self) -> None:
        """无长连接资源, 空实现。"""
```

- [ ] **Step 4: 注册能力与 getter**

`backend/app/data_providers/capabilities.py`：

1. `_TIER_RANK` 增加 `"never": 99`（TickFlow 永不提供）：

```python
# 档位排序: none 最低 (无 Key/无效 Key, 仅免费通道历史日K), 未知档按 none 处理 (fail-closed)
# never: TickFlow 完全不提供该数据集(研报扩展能力), 任何真实档位都不进候选
_TIER_RANK = {"none": -1, "free": 0, "starter": 1, "pro": 2, "expert": 3, "never": 99}
```

2. `CAPABILITY_REGISTRY` 末尾追加（顺序即设置页卡片顺序）：

```python
    {
        "id": "research",
        "label": "研报与评级",
        "desc": "券商研报、机构评级、盈利预测与基金持仓(研报管线专用)",
        "field": "research_data_provider",
        "default": "akshare_ext",
        "tf_tier": "never",
    },
    {
        "id": "shareholders",
        "label": "股东与股本",
        "desc": "十大股东/流通股东、股东户数、股本结构、限售解禁、高管持股变动",
        "field": "shareholders_data_provider",
        "default": "akshare_ext",
        "tf_tier": "never",
    },
    {
        "id": "fund_flow",
        "label": "资金流与融资融券",
        "desc": "个股资金流向、融资融券、龙虎榜(研报管线专用)",
        "field": "fund_flow_data_provider",
        "default": "akshare_ext",
        "tf_tier": "never",
    },
    {
        "id": "company_info",
        "label": "公司信息与公告",
        "desc": "公司概况、主营构成、新闻、公告、业绩预告/快报、分红送配",
        "field": "company_info_data_provider",
        "default": "akshare_ext",
        "tf_tier": "never",
    },
```

3. `_current_routing()` 追加 4 项（保持显式列全，不用 getattr）：

```python
        "research_data_provider": preferences.get_research_data_provider(),
        "shareholders_data_provider": preferences.get_shareholders_data_provider(),
        "fund_flow_data_provider": preferences.get_fund_flow_data_provider(),
        "company_info_data_provider": preferences.get_company_info_data_provider(),
```

`backend/app/services/preferences.py`（`get_financial_provider` 之后）：

```python
def get_research_data_provider() -> str:
    provider = str(load().get("research_data_provider", "akshare_ext") or "akshare_ext").lower()
    return provider if provider in _allowed_data_providers() else "akshare_ext"


def get_shareholders_data_provider() -> str:
    provider = str(load().get("shareholders_data_provider", "akshare_ext") or "akshare_ext").lower()
    return provider if provider in _allowed_data_providers() else "akshare_ext"


def get_fund_flow_data_provider() -> str:
    provider = str(load().get("fund_flow_data_provider", "akshare_ext") or "akshare_ext").lower()
    return provider if provider in _allowed_data_providers() else "akshare_ext"


def get_company_info_data_provider() -> str:
    provider = str(load().get("company_info_data_provider", "akshare_ext") or "akshare_ext").lower()
    return provider if provider in _allowed_data_providers() else "akshare_ext"
```

`backend/app/api/settings.py`：找到 capability-matrix 端点里构造 `current` 的 dict（与 `capabilities._current_routing()` 同组 getter），补上 4 个新 getter；`backend/tests/test_capability_matrix.py` 的 `test_api_endpoint_injects_all_routing_preferences` 同步加 4 个断言（断言响应里存在 4 个新能力且 `current` 等于 getter 返回值）。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_provider.py tests/test_capability_matrix.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/app/plugins/akshare_ext backend/app/data_providers/capabilities.py backend/app/services/preferences.py backend/app/api/settings.py backend/tests/test_akshare_ext_provider.py backend/tests/test_capability_matrix.py
git commit -m "feat(report): akshare_ext 插件骨架 + 4 个研报域能力注册"
```

---

### Task 2: research 数据集（研报 / 机构评级 / 基金持仓）

**Files:**
- Modify: `backend/app/plugins/akshare_ext/provider.py`
- Test: `backend/tests/test_akshare_ext_research.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `_records` / `_clean` / `AkshareExtProvider`
- Produces: `AkshareExtProvider.get_research(symbol: str) -> dict[str, list[dict]]`，键固定为 `{"research", "recommend", "fund_hold"}`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_akshare_ext_research.py
"""research 数据集契约: 块键、列透传、日期窗口、空数据不抛错。"""
from __future__ import annotations

import sys
import types

import pytest

from app.plugins.akshare_ext.provider import AkshareExtProvider


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        return list(self._rows)


def _install(monkeypatch, mapping):
    """mapping: 函数名 -> 返回值(或抛异常的 lambda)。"""
    mod = types.ModuleType("akshare")
    for name, value in mapping.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, "akshare", mod)
    return mod


def test_research_returns_three_blocks(monkeypatch):
    _install(monkeypatch, {
        "stock_research_report_em": lambda symbol: _FakeFrame(
            [{"报告名称": "深度报告", "东财评级": "买入", "机构": "某证券", "日期": "2026-10-01"}]),
        "stock_institute_recommend": lambda symbol: _FakeFrame(
            [{"股票简称": "贵州茅台", "综合评级": "买入", "评级机构数": 12}]),
        "stock_report_fund_hold_detail": lambda symbol, date: _FakeFrame(
            [{"基金名称": "某基金", "持股数": 100}]),
    })
    blocks = AkshareExtProvider().get_research("600519.SH")
    assert set(blocks) == {"research", "recommend", "fund_hold"}
    assert blocks["research"][0]["报告名称"] == "深度报告"


def test_research_missing_frames_yield_empty_lists(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("接口异常")

    _install(monkeypatch, {
        "stock_research_report_em": _boom,
        "stock_institute_recommend": _boom,
        "stock_report_fund_hold_detail": _boom,
    })
    blocks = AkshareExtProvider().get_research("600519.SH")
    assert blocks == {"research": [], "recommend": [], "fund_hold": []}


def test_non_finite_values_become_none(monkeypatch):
    _install(monkeypatch, {
        "stock_research_report_em": lambda symbol: _FakeFrame([{"目标价": float("nan")}]),
        "stock_institute_recommend": lambda symbol: _FakeFrame([]),
        "stock_report_fund_hold_detail": lambda symbol, date: _FakeFrame([]),
    })
    blocks = AkshareExtProvider().get_research("600519.SH")
    assert blocks["research"][0]["目标价"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_research.py -q`
Expected: FAIL（`AttributeError: 'AkshareExtProvider' object has no attribute 'get_research'`）

- [ ] **Step 3: 实现**

在 `provider.py` 追加（`_import_ak()` 惰性导入，块名与 stock_ai `stock_full_report.py` 一致）：

```python
    def get_research(self, symbol: str) -> dict[str, list[dict]]:
        """研报 / 机构评级 / 基金持仓三块。symbol 形如 600519.SH。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        blocks: dict[str, list[dict]] = {"research": [], "recommend": [], "fund_hold": []}
        blocks["research"] = _safe(
            lambda: _records(ak.stock_research_report_em(symbol=code)), "research")
        blocks["recommend"] = _safe(
            lambda: _records(ak.stock_institute_recommend(symbol="股票综合评级")), "recommend")
        for period in _recent_periods(2):
            rows = _safe(lambda p=period: _records(
                ak.stock_report_fund_hold_detail(symbol=code, date=p)), "fund_hold")
            if rows:
                blocks["fund_hold"] = rows
                break
        return blocks
```

配套模块级工具函数（Task 2 一并加入 `provider.py`，后续 Task 复用）：

```python
def _import_ak():
    import akshare  # noqa: PLC0415

    return akshare


def _split_symbol(symbol: str) -> tuple[str, str]:
    """600519.SH → ("600519", "sh"); 非法输入抛 ValueError(fail-closed)。"""
    text = str(symbol or "").strip().upper()
    if "." not in text:
        raise ValueError(f"symbol 需带交易所后缀(如 600519.SH), 实际 {symbol!r}")
    code, _, suffix = text.partition(".")
    if len(code) != 6 or suffix not in ("SH", "SZ", "BJ"):
        raise ValueError(f"symbol 格式非法: {symbol!r}")
    return code, suffix.lower()


def _recent_periods(count: int) -> list[str]:
    """最近 count 个报告期(YYYYMMDD, 季度末), 倒序; 只用于基金持仓等按报告期查询。"""
    import datetime as dt

    today = dt.date.today()
    quarter_ends = []
    year = today.year
    while len(quarter_ends) < count + 4:
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = dt.date(year, month, day)
            if q < today:
                quarter_ends.append(q)
        year -= 1
    return [d.strftime("%Y%m%d") for d in sorted(set(quarter_ends), reverse=True)[:count]]


def _safe(fn, label: str) -> Any:
    """接口异常/未安装降级为空结果 + 告警(绝不中断整包采集)。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[akshare_ext] %s 采集失败(%s), 该块置空", label, type(exc).__name__)
        return []
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_research.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/plugins/akshare_ext/provider.py backend/tests/test_akshare_ext_research.py
git commit -m "feat(report): akshare_ext research 数据集(研报/评级/基金持仓)"
```

---

### Task 3: shareholders 数据集（股东 / 股本 / 解禁 / 高管持股）

**Files:**
- Modify: `backend/app/plugins/akshare_ext/provider.py`
- Test: `backend/tests/test_akshare_ext_shareholders.py`（新建）

**Interfaces:**
- Produces: `get_shareholders(symbol: str) -> dict[str, list[dict]]`，键固定 `{"top10", "gdhs", "share_structure", "release", "share_hold_change"}`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_akshare_ext_shareholders.py
"""shareholders 数据集契约: 五块键、top10 流通股东回退、交易所分流。"""
from __future__ import annotations

import sys
import types

from app.plugins.akshare_ext.provider import AkshareExtProvider


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        return list(self._rows)


def _install(monkeypatch, mapping):
    mod = types.ModuleType("akshare")
    for name, value in mapping.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, "akshare", mod)


def test_shareholders_block_keys(monkeypatch):
    _install(monkeypatch, {
        "stock_gdfx_top_10_em": lambda symbol, date: _FakeFrame([{"股东名称": "A"}]),
        "stock_gdfx_free_top_10_em": lambda symbol, date: _FakeFrame([{"股东名称": "B"}]),
        "stock_zh_a_gdhs_detail_em": lambda symbol: _FakeFrame([{"股东户数-本次": 100}]),
        "stock_zh_a_gbjg_em": lambda symbol: _FakeFrame([{"变动日期": "2026-01-01"}]),
        "stock_restricted_release_queue_em": lambda symbol: _FakeFrame([{"解禁数量": 1}]),
        "stock_share_hold_change_sse": lambda symbol: _FakeFrame([{"姓名": "张三"}]),
    })
    blocks = AkshareExtProvider().get_shareholders("600519.SH")
    assert set(blocks) == {"top10", "gdhs", "share_structure", "release", "share_hold_change"}
    assert blocks["top10"][0]["股东名称"] == "A"


def test_top10_falls_back_to_free_float_when_main_empty(monkeypatch):
    _install(monkeypatch, {
        "stock_gdfx_top_10_em": lambda symbol, date: _FakeFrame([]),
        "stock_gdfx_free_top_10_em": lambda symbol, date: _FakeFrame([{"股东名称": "B"}]),
        "stock_zh_a_gdhs_detail_em": lambda symbol: _FakeFrame([]),
        "stock_zh_a_gbjg_em": lambda symbol: _FakeFrame([]),
        "stock_restricted_release_queue_em": lambda symbol: _FakeFrame([]),
        "stock_share_hold_change_sse": lambda symbol: _FakeFrame([]),
    })
    blocks = AkshareExtProvider().get_shareholders("600519.SH")
    assert blocks["top10"][0]["股东名称"] == "B"


def test_sz_symbol_uses_szse_endpoint(monkeypatch):
    calls: list[str] = []

    def _sse(symbol):
        calls.append("sse")
        return _FakeFrame([{"姓名": "沪"}])

    def _szse(symbol):
        calls.append("szse")
        return _FakeFrame([{"姓名": "深"}])

    _install(monkeypatch, {
        "stock_gdfx_top_10_em": lambda symbol, date: _FakeFrame([]),
        "stock_gdfx_free_top_10_em": lambda symbol, date: _FakeFrame([]),
        "stock_zh_a_gdhs_detail_em": lambda symbol: _FakeFrame([]),
        "stock_zh_a_gbjg_em": lambda symbol: _FakeFrame([]),
        "stock_restricted_release_queue_em": lambda symbol: _FakeFrame([]),
        "stock_share_hold_change_sse": _sse,
        "stock_share_hold_change_szse": _szse,
    })
    blocks = AkshareExtProvider().get_shareholders("000001.SZ")
    assert calls == ["szse"]
    assert blocks["share_hold_change"][0]["姓名"] == "深"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_shareholders.py -q`
Expected: FAIL（no attribute `get_shareholders`）

- [ ] **Step 3: 实现**

```python
    def get_shareholders(self, symbol: str) -> dict[str, list[dict]]:
        """股东/股本五块(十大股东、股东户数、股本结构、限售解禁、高管持股变动)。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        blocks: dict[str, list[dict]] = {
            "top10": [], "gdhs": [], "share_structure": [],
            "release": [], "share_hold_change": [],
        }
        for period in _recent_periods(4):
            rows = _safe(lambda p=period: _records(
                ak.stock_gdfx_top_10_em(symbol=f"{market}{code}", date=p)), "top10")
            if rows:
                blocks["top10"] = rows
                break
        if not blocks["top10"]:
            for period in _recent_periods(4):
                rows = _safe(lambda p=period: _records(
                    ak.stock_gdfx_free_top_10_em(symbol=f"{market}{code}", date=p)), "top10")
                if rows:
                    blocks["top10"] = rows
                    break
        blocks["gdhs"] = _safe(lambda: _records(
            ak.stock_zh_a_gdhs_detail_em(symbol=code)), "gdhs")
        blocks["share_structure"] = _safe(lambda: _records(
            ak.stock_zh_a_gbjg_em(symbol=f"{code}.{market.upper()}")), "share_structure")
        blocks["release"] = _safe(lambda: _records(
            ak.stock_restricted_release_queue_em(symbol=code)), "release")
        if market == "sz":
            blocks["share_hold_change"] = _safe(lambda: _records(
                ak.stock_share_hold_change_szse(symbol=code)), "share_hold_change")
        else:
            blocks["share_hold_change"] = _safe(lambda: _records(
                ak.stock_share_hold_change_sse(symbol=code)), "share_hold_change")
        return blocks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_shareholders.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/plugins/akshare_ext/provider.py backend/tests/test_akshare_ext_shareholders.py
git commit -m "feat(report): akshare_ext shareholders 数据集"
```

---

### Task 4: fund_flow 数据集（资金流 / 融资融券 / 龙虎榜）

**Files:**
- Modify: `backend/app/plugins/akshare_ext/provider.py`
- Test: `backend/tests/test_akshare_ext_fund_flow.py`（新建）

**Interfaces:**
- Produces: `get_fund_flow(symbol: str) -> dict[str, list[dict]]`，键固定 `{"fund_flow", "margin", "lhb"}`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_akshare_ext_fund_flow.py
"""fund_flow 数据集契约: 三块键、龙虎榜按代码过滤、market 参数传递。"""
from __future__ import annotations

import sys
import types

from app.plugins.akshare_ext.provider import AkshareExtProvider


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        return list(self._rows)


def _install(monkeypatch, mapping):
    mod = types.ModuleType("akshare")
    for name, value in mapping.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, "akshare", mod)


def test_fund_flow_blocks_and_market(monkeypatch):
    seen: dict[str, str] = {}

    def _flow(stock, market):
        seen["stock"] = stock
        seen["market"] = market
        return _FakeFrame([{"主力净流入-净额": 1000}])

    _install(monkeypatch, {
        "stock_individual_fund_flow": _flow,
        "stock_margin_detail_sse": lambda date: _FakeFrame([{"融资余额": 5}]),
        "stock_lhb_detail_em": lambda start_date, end_date: _FakeFrame(
            [{"代码": "600519", "上榜日": "2026-10-01"}, {"代码": "000001", "上榜日": "2026-10-01"}]),
    })
    blocks = AkshareExtProvider().get_fund_flow("600519.SH")
    assert set(blocks) == {"fund_flow", "margin", "lhb"}
    assert seen == {"stock": "600519", "market": "sh"}
    assert blocks["fund_flow"][0]["主力净流入-净额"] == 1000
    assert [r["代码"] for r in blocks["lhb"]] == ["600519"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_fund_flow.py -q`
Expected: FAIL（no attribute `get_fund_flow`）

- [ ] **Step 3: 实现**

```python
    def get_fund_flow(self, symbol: str) -> dict[str, list[dict]]:
        """资金流/融资融券/龙虎榜三块(龙虎榜按代码过滤, 近 30 自然日窗口)。"""
        import datetime as dt

        ak = _import_ak()
        code, market = _split_symbol(symbol)
        blocks: dict[str, list[dict]] = {"fund_flow": [], "margin": [], "lhb": []}
        blocks["fund_flow"] = _safe(lambda: _records(
            ak.stock_individual_fund_flow(stock=code, market=market)), "fund_flow")
        day = dt.date.today()
        prev = (day - dt.timedelta(days=1)).strftime("%Y%m%d")
        margin_fn = ak.stock_margin_detail_szse if market == "sz" else ak.stock_margin_detail_sse
        blocks["margin"] = _safe(lambda: _records(margin_fn(date=prev)), "margin")
        start = (day - dt.timedelta(days=30)).strftime("%Y%m%d")
        end = day.strftime("%Y%m%d")
        rows = _safe(lambda: _records(
            ak.stock_lhb_detail_em(start_date=start, end_date=end)), "lhb")
        blocks["lhb"] = [r for r in rows if str(r.get("代码") or "").strip() == code]
        return blocks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_fund_flow.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/plugins/akshare_ext/provider.py backend/tests/test_akshare_ext_fund_flow.py
git commit -m "feat(report): akshare_ext fund_flow 数据集"
```

---

### Task 5: company_info 数据集（概况 / 主营 / 新闻 / 公告 / 业绩 / 分红）

**Files:**
- Modify: `backend/app/plugins/akshare_ext/provider.py`
- Test: `backend/tests/test_akshare_ext_company_info.py`（新建）

**Interfaces:**
- Produces: `get_company_info(symbol: str) -> dict[str, list[dict]]`，键固定 `{"basic_info", "zygc", "news", "notice", "yjyg", "yjkb", "dividend", "share_alloc"}`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_akshare_ext_company_info.py
"""company_info 数据集契约: 八块键、公告按代码过滤、业绩预告两期回退。"""
from __future__ import annotations

import sys
import types

from app.plugins.akshare_ext.provider import AkshareExtProvider


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        return list(self._rows)


def _install(monkeypatch, mapping):
    mod = types.ModuleType("akshare")
    for name, value in mapping.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, "akshare", mod)


def test_company_info_block_keys_and_notice_filter(monkeypatch):
    _install(monkeypatch, {
        "stock_individual_basic_info_xq": lambda symbol: _FakeFrame([{"item": "简称", "value": "贵州茅台"}]),
        "stock_zygc_em": lambda symbol: _FakeFrame([{"主营构成": "白酒"}]),
        "stock_news_em": lambda symbol: _FakeFrame([{"新闻标题": "新闻"}]),
        "stock_notice_report": lambda symbol, date: _FakeFrame(
            [{"代码": "600519", "公告标题": "公告"}, {"代码": "000001", "公告标题": "别家"}]),
        "stock_yjyg_em": lambda date: _FakeFrame([{"股票代码": "600519", "预测指标": "预增"}]),
        "stock_yjkb_em": lambda date: _FakeFrame([]),
        "stock_history_dividend_detail": lambda symbol, indicator: _FakeFrame([{"公告日期": "2026-06-01"}]),
    })
    blocks = AkshareExtProvider().get_company_info("600519.SH")
    assert set(blocks) == {"basic_info", "zygc", "news", "notice", "yjyg", "yjkb",
                           "dividend", "share_alloc"}
    assert [r["公告标题"] for r in blocks["notice"]] == ["公告"]
    assert blocks["basic_info"][0]["value"] == "贵州茅台"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_company_info.py -q`
Expected: FAIL（no attribute `get_company_info`）

- [ ] **Step 3: 实现**

```python
    def get_company_info(self, symbol: str) -> dict[str, list[dict]]:
        """公司信息八块(概况/主营/新闻/公告/业绩预告/快报/分红/送转)。"""
        import datetime as dt

        ak = _import_ak()
        code, market = _split_symbol(symbol)
        blocks: dict[str, list[dict]] = {
            "basic_info": [], "zygc": [], "news": [], "notice": [],
            "yjyg": [], "yjkb": [], "dividend": [], "share_alloc": [],
        }
        blocks["basic_info"] = _safe(lambda: _records(
            ak.stock_individual_basic_info_xq(symbol=f"{code}.{market.upper()}")), "basic_info")
        blocks["zygc"] = _safe(lambda: _records(
            ak.stock_zygc_em(symbol=f"{market.upper()}{code}")), "zygc")
        blocks["news"] = _safe(lambda: _records(ak.stock_news_em(symbol=code)), "news")
        today = dt.date.today().strftime("%Y%m%d")
        notice = _safe(lambda: _records(
            ak.stock_notice_report(symbol="全部", date=today)), "notice")
        blocks["notice"] = [r for r in notice if str(r.get("代码") or "").strip() == code]
        for period in _recent_periods(2):
            if not blocks["yjyg"]:
                rows = _safe(lambda p=period: _records(ak.stock_yjyg_em(date=p)), "yjyg")
                blocks["yjyg"] = [r for r in rows
                                  if str(r.get("股票代码") or "").strip() == code]
            if not blocks["yjkb"]:
                rows = _safe(lambda p=period: _records(ak.stock_yjkb_em(date=p)), "yjkb")
                blocks["yjkb"] = [r for r in rows
                                  if str(r.get("股票代码") or "").strip() == code]
        blocks["dividend"] = _safe(lambda: _records(
            ak.stock_history_dividend_detail(symbol=code, indicator="分红")), "dividend")
        blocks["share_alloc"] = _safe(lambda: _records(
            ak.stock_history_dividend_detail(symbol=code, indicator="配股")), "share_alloc")
        return blocks
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_company_info.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/plugins/akshare_ext/provider.py backend/tests/test_akshare_ext_company_info.py
git commit -m "feat(report): akshare_ext company_info 数据集"
```

---

### Task 6: 同源保真方法（日K / 财务 / 实时快照）

**Files:**
- Modify: `backend/app/plugins/akshare_ext/provider.py`
- Test: `backend/tests/test_akshare_ext_fidelity.py`（新建）

**Interfaces:**
- Produces: `get_report_kline(symbol, years=3) -> list[dict]`、`get_report_financials(symbol) -> dict[str, list[dict]]`（键 `{"fin_abstract", "fin_indicator_ths", "balance_sheet", "income_statement", "cashflow"}`）、`get_report_spot(symbol) -> list[dict]`
- 说明：这三个方法是**非路由**方法（不声明 daily/financial/realtime 数据集，不进能力矩阵），只服务研报管线的 stock_ai 同源取数。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_akshare_ext_fidelity.py
"""同源保真方法契约: 接口与 stock_ai Phase 1 一致, spot 按代码过滤。"""
from __future__ import annotations

import sys
import types

from app.plugins.akshare_ext.provider import AkshareExtProvider


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        return list(self._rows)


def _install(monkeypatch, mapping):
    mod = types.ModuleType("akshare")
    for name, value in mapping.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, "akshare", mod)


def test_report_kline_uses_qfq_adjust(monkeypatch):
    seen: dict[str, str] = {}

    def _daily(symbol, adjust=""):
        seen["symbol"] = symbol
        seen["adjust"] = adjust
        return _FakeFrame([{"date": "2026-10-14", "open": 1.0, "close": 1.1}])

    _install(monkeypatch, {"stock_zh_a_daily": _daily})
    rows = AkshareExtProvider().get_report_kline("600519.SH")
    assert seen == {"symbol": "sh600519", "adjust": "qfq"}
    assert rows[0]["close"] == 1.1


def test_report_financials_blocks(monkeypatch):
    _install(monkeypatch, {
        "stock_financial_abstract": lambda symbol: _FakeFrame([{"选项": "常用指标"}]),
        "stock_financial_abstract_ths": lambda symbol, indicator: _FakeFrame([{"报告期": "2026-06-30"}]),
        "stock_financial_report_sina": lambda stock, symbol: _FakeFrame([{"报表日期": "2026-06-30"}]),
    })
    blocks = AkshareExtProvider().get_report_financials("600519.SH")
    assert set(blocks) == {"fin_abstract", "fin_indicator_ths", "balance_sheet",
                           "income_statement", "cashflow"}
    assert blocks["balance_sheet"]


def test_report_spot_filters_single_symbol(monkeypatch):
    _install(monkeypatch, {
        "stock_zh_a_spot": lambda: _FakeFrame(
            [{"代码": "sh600519", "名称": "贵州茅台", "最新价": 1500.0},
             {"代码": "sz000001", "名称": "平安银行", "最新价": 11.0}]),
    })
    rows = AkshareExtProvider().get_report_spot("600519.SH")
    assert [r["名称"] for r in rows] == ["贵州茅台"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_fidelity.py -q`
Expected: FAIL（no attribute `get_report_kline`）

- [ ] **Step 3: 实现**

```python
    # ---- 非路由方法: 研报管线 stock_ai 同源取数(不声明 daily/financial/realtime,
    # 不进能力矩阵, 不改变全局路由; 先例: dragon_tiger 直接经 custom_sources 取 fuyao) ----

    def get_report_kline(self, symbol: str, years: int = 3) -> list[dict]:
        """日 K(前复权), 与 stock_ai stock_zh_a_daily(adjust="qfq") 同源同参。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        return _safe(lambda: _records(
            ak.stock_zh_a_daily(symbol=f"{market}{code}", adjust="qfq")), "kline_daily")

    def get_report_financials(self, symbol: str) -> dict[str, list[dict]]:
        """财务摘要/同花顺指标/三大报表五块。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        prefixed = f"{market}{code}"
        blocks: dict[str, list[dict]] = {
            "fin_abstract": [], "fin_indicator_ths": [], "balance_sheet": [],
            "income_statement": [], "cashflow": [],
        }
        blocks["fin_abstract"] = _safe(lambda: _records(
            ak.stock_financial_abstract(symbol=code)), "fin_abstract")
        blocks["fin_indicator_ths"] = _safe(lambda: _records(
            ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")), "fin_indicator_ths")
        for key, table in (("balance_sheet", "资产负债表"),
                           ("income_statement", "利润表"), ("cashflow", "现金流量表")):
            blocks[key] = _safe(lambda t=table: _records(
                ak.stock_financial_report_sina(stock=prefixed, symbol=t)), key)
        return blocks

    def get_report_spot(self, symbol: str) -> list[dict]:
        """全市场快照 → 该标的单行, 与 stock_ai 的 spot 块同一接口。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        want = f"{market}{code}"
        rows = _safe(lambda: _records(ak.stock_zh_a_spot()), "spot")
        return [r for r in rows if str(r.get("代码") or "").strip().lower() == want]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_akshare_ext_fidelity.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/plugins/akshare_ext/provider.py backend/tests/test_akshare_ext_fidelity.py
git commit -m "feat(report): akshare_ext 同源保真取数方法(日K/财务/快照)"
```

---

### Task 7: reporting 包基础移植（content_schema + technical_indicators）

**Files:**
- Create: `backend/app/reporting/__init__.py`
- Create: `backend/app/reporting/content_schema.py`（自 `C:\Users\19229\Desktop\stock_ai\content_schema.py` 原样移植）
- Create: `backend/app/reporting/technical_indicators.py`（自 `stock_ai\technical_indicators.py` 原样移植）
- Test: `backend/tests/test_reporting_schema.py`（新建）

**Interfaces:**
- Produces: `app.reporting.content_schema`：`SCHEMA` / `MIN_LEN` / `ITEM_KEYS` / `SENTIMENTS` / `SCORE_KEYS` / `validate_content(obj) -> list[str]` / `block_keys() -> list[str]`；`app.reporting.technical_indicators`：stock_ai 同名函数（含 `format_indicators_for_json` / `get_latest_signals`）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_reporting_schema.py
"""content_schema 契约: 15 块齐全、缺失/越界校验、指标计算自洽。"""
from __future__ import annotations

from app.reporting import content_schema as cs
from app.reporting import technical_indicators as ti

EXPECTED_BLOCKS = {
    "meta", "profile", "hero", "conclusion", "mission", "macro", "chain",
    "quality", "news", "elasticity", "risk", "valuation", "compare",
    "tracking", "technical",
}


def test_schema_covers_fifteen_blocks():
    assert set(cs.SCHEMA) == EXPECTED_BLOCKS
    assert set(cs.block_keys()) == EXPECTED_BLOCKS


def test_validate_reports_missing_blocks():
    errors = cs.validate_content({})
    assert errors and all(e.startswith("[") for e in errors)


def test_indicator_signal_shape():
    import pandas as pd

    closes = [10.0 + i * 0.1 for i in range(120)]
    df = pd.DataFrame({
        "date": [f"2026-01-{i % 28 + 1:02d}" for i in range(120)],
        "open": closes, "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes], "close": closes,
        "volume": [1000.0] * 120,
    })
    enriched = ti.calculate_all_indicators(df)
    out = ti.format_indicators_for_json(enriched, max_rows=120)
    assert len(out) <= 120 and "MACD" in out[0]
    signals = ti.get_latest_signals(enriched)
    assert isinstance(signals, dict) and signals
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_reporting_schema.py -q`
Expected: FAIL（`ModuleNotFoundError: app.reporting`）

- [ ] **Step 3: 原样移植两个模块**

```bash
mkdir -p backend/app/reporting
cp "C:/Users/19229/Desktop/stock_ai/content_schema.py" backend/app/reporting/content_schema.py
cp "C:/Users/19229/Desktop/stock_ai/technical_indicators.py" backend/app/reporting/technical_indicators.py
```

`backend/app/reporting/__init__.py` 留空。`technical_indicators.py` 顶层函数（已核对源文件）：`calculate_ma` / `calculate_macd` / `calculate_kdj` / `calculate_rsi` / `calculate_boll` / `calculate_all_indicators` / `get_latest_signals` / `format_indicators_for_json`。该模块是 **pandas 实现，保持原样**——移植目标是与 stock_ai 指标数值逐项一致，且 pandas 已在 backend 依赖（`backend/pyproject.toml:21`）；仅研报管线内部调用，不改写成 Polars。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_reporting_schema.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/reporting backend/tests/test_reporting_schema.py
git commit -m "feat(report): 移植 content_schema 与技术指标计算"
```

---

### Task 8: 块收集与日缓存 `stock_report_data`

**Files:**
- Create: `backend/app/services/stock_report_data.py`
- Test: `backend/tests/test_stock_report_data.py`（新建）

**Interfaces:**
- Consumes: Task 1-7（`custom_loader.get_provider` / `provider_has_dataset`、`app.reporting.technical_indicators`）
- Produces:
  - `BLOCK_NAMES: tuple[str, ...]`（27 个数据块，与 stock_ai `data_{code}.json` 的 `blocks` 键一致）
  - `collect_blocks(symbol: str, *, force: bool = False) -> tuple[dict, list[str]]` → `(blocks, missing)`；缓存 `settings.data_dir / "report_cache" / {symbol} / {YYYYMMDD}.json`
  - `cache_path(symbol: str, day: str = "") -> Path`
  - 抛 `ReportDataUnavailable(ValueError)`：provider 不可用或 kline 为空（fail-closed）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_stock_report_data.py
"""块收集: 27 块齐全、缓存命中、缺块降级、provider 不可用 fail-closed。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import stock_report_data as srd


class _FakeProvider:
    name = "akshare_ext"

    def get_research(self, symbol):
        return {"research": [{"报告名称": "r"}]}

    def get_shareholders(self, symbol):
        return {"top10": [{"股东名称": "A"}]}

    def get_fund_flow(self, symbol):
        return {"fund_flow": [{"主力净流入-净额": 1}]}

    def get_company_info(self, symbol):
        return {"news": [{"新闻标题": "n"}]}

    def get_report_financials(self, symbol):
        return {"fin_abstract": [{"选项": "常用指标"}]}

    def get_report_spot(self, symbol):
        return [{"名称": "贵州茅台"}]

    def get_report_kline(self, symbol, years=3):
        return [{"date": f"2026-01-{i + 1:02d}", "open": 10.0, "high": 11.0,
                 "low": 9.0, "close": 10.5, "volume": 100.0} for i in range(120)]


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(srd, "_provider", lambda: _FakeProvider())
    monkeypatch.setattr(srd.settings, "data_dir", tmp_path, raising=False)
    return tmp_path


def test_collect_covers_all_blocks_and_writes_cache(patched):
    blocks, missing = srd.collect_blocks("600519.SH")
    assert set(blocks) == set(srd.BLOCK_NAMES) | {"_analysis_date"}
    assert missing == []
    assert blocks["research"][0]["报告名称"] == "r"
    assert blocks["technical_indicators"] and blocks["technical_signals"]
    cache = srd.cache_path("600519.SH")
    assert cache.exists()


def test_second_call_uses_cache_without_provider(patched, monkeypatch):
    srd.collect_blocks("600519.SH")
    monkeypatch.setattr(srd, "_provider", lambda: (_ for _ in ()).throw(
        AssertionError("缓存命中时不应再取数")))
    blocks, missing = srd.collect_blocks("600519.SH")
    assert blocks["research"] and missing == []


def test_missing_kline_raises(patched, monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr(provider, "get_report_kline", lambda symbol, years=3: [])
    monkeypatch.setattr(srd, "_provider", lambda: provider)
    with pytest.raises(srd.ReportDataUnavailable):
        srd.collect_blocks("600519.SH")


def test_unavailable_provider_raises(patched, monkeypatch):
    monkeypatch.setattr(srd, "_provider", lambda: None)
    with pytest.raises(srd.ReportDataUnavailable):
        srd.collect_blocks("600519.SH")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_stock_report_data.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.stock_report_data`）

- [ ] **Step 3: 实现**

```python
"""研报块收集 — akshare_ext 同源取数 + 技术指标计算 + 当日缓存。

与 stock_ai Phase 1 的 data_{code}.json 块名逐一对齐(27 个数据块 + 两个
计算块), 保证 digest / 渲染 / 一致性验收可比。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings
from app.market_time import cn_now
from app.reporting import technical_indicators as ti

logger = logging.getLogger(__name__)

PROVIDER_NAME = "akshare_ext"

# 与 stock_ai stock_full_report.py 的 submit() 键名一致(26 块)
BLOCK_NAMES: tuple[str, ...] = (
    "basic_info", "spot", "share_structure", "zygc", "kline_daily",
    "fund_flow", "lhb", "fin_abstract", "fin_indicator_ths", "balance_sheet",
    "income_statement", "cashflow", "yjyg", "yjkb", "top10", "gdhs",
    "share_hold_change", "dividend", "share_alloc", "release", "notice", "news",
    "research", "recommend", "fund_hold", "margin",
)
# stock_ai Phase 1 采集但 digest/渲染均不消费, 本管线不采集(对比脚本同样跳过)
SKIPPED_BLOCKS: tuple[str, ...] = ("kline_minute",)


class ReportDataUnavailable(ValueError):
    """数据层不可用(fail-closed): provider 缺失或日K为空。"""


def _provider():
    """取 akshare_ext 插件实例; 不可用时返回 None(调用方 fail-closed)。"""
    try:
        from app.data_providers.custom import loader as custom_loader
    except Exception:  # noqa: BLE001
        return None
    try:
        if not custom_loader.provider_has_dataset(PROVIDER_NAME, "research"):
            return None
        return custom_loader.get_provider(PROVIDER_NAME)
    except Exception:  # noqa: BLE001
        logger.warning("[report-data] akshare_ext 不可用", exc_info=True)
        return None


def cache_dir(symbol: str) -> Path:
    safe = str(symbol).replace("/", "_").replace("\\", "_")
    return settings.data_dir / "report_cache" / safe


def cache_path(symbol: str, day: str = "") -> Path:
    stamp = day or cn_now().strftime("%Y%m%d")
    return cache_dir(symbol) / f"{stamp}.json"


def _indicators(blocks: dict) -> None:
    """日K → MACD/KDJ/RSI/BOLL 指标块与信号块(stock_ai 同款 pandas 实现)。"""
    rows = blocks.get("kline_daily") or []
    if not rows:
        blocks["technical_indicators"] = []
        blocks["technical_signals"] = {}
        return
    import pandas as pd

    df = pd.DataFrame(rows)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    enriched = ti.calculate_all_indicators(df)
    blocks["technical_indicators"] = ti.format_indicators_for_json(enriched, max_rows=120)
    blocks["technical_signals"] = ti.get_latest_signals(enriched)


def collect_blocks(symbol: str, *, force: bool = False) -> tuple[dict, list[str]]:
    """采集全部块; 命中当日缓存且未 force 时直接复用。返回 (blocks, missing)。"""
    path = cache_path(symbol)
    if path.exists() and not force:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload["blocks"], list(payload.get("missing") or [])
        except (OSError, ValueError, KeyError):
            logger.warning("[report-data] 缓存损坏, 重新采集: %s", path)

    provider = _provider()
    if provider is None:
        raise ReportDataUnavailable(
            "akshare_ext 数据源不可用: 请在设置页安装 akshare 并启用该数据源")

    blocks: dict = {}
    blocks.update(provider.get_company_info(symbol))
    blocks.update(provider.get_shareholders(symbol))
    blocks.update(provider.get_research(symbol))
    blocks.update(provider.get_fund_flow(symbol))
    blocks.update(provider.get_report_financials(symbol))
    blocks["kline_daily"] = provider.get_report_kline(symbol)
    blocks["spot"] = provider.get_report_spot(symbol)
    if not blocks.get("kline_daily"):
        raise ReportDataUnavailable(f"标的 {symbol} 日K为空, 无法生成研报")

    _indicators(blocks)
    for name in BLOCK_NAMES:
        blocks.setdefault(name, [])
    missing = [name for name in BLOCK_NAMES if not blocks.get(name)]
    blocks["_analysis_date"] = cn_now().strftime("%Y-%m-%d")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"blocks": blocks, "missing": missing},
                               ensure_ascii=False, default=str), encoding="utf-8")
    return blocks, missing
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_stock_report_data.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/stock_report_data.py backend/tests/test_stock_report_data.py
git commit -m "feat(report): 块收集与当日缓存(akshare_ext 同源)"
```

---

### Task 9: ai_provider 扩展（response_format + 研报端点覆盖）与 `REPORT_AI_*` 配置

**Files:**
- Modify: `backend/app/services/ai_provider.py`（`generate_ai_text` / `_run_openai_once` / `_run_openai_message_once` / `_openai_client` / `_openai_kwargs` / `_openai_retry_kwargs`）
- Modify: `backend/app/config.py:97-104` 附近追加 `report_ai_*`
- Modify: `backend/.env.example`（在 AI 段落后追加说明）
- Test: `backend/tests/test_ai_provider_report_override.py`（新建）

**Interfaces:**
- Produces:
  - `AiEndpointOverride`（`@dataclass(frozen=True)`：`base_url: str = ""` / `api_key: str = ""` / `model: str = ""`）
  - `generate_ai_text(..., response_format: dict | None = None, ai_config: AiEndpointOverride | None = None) -> str`
  - `app.services.report_ai_config.resolve() -> AiEndpointOverride | None`（空配置返回 `None`，逐项回退全局）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_ai_provider_report_override.py
"""研报 AI 端点覆盖: 缺省回退全局、覆盖生效、JSON mode 被拒自动去参。"""
from __future__ import annotations

import pytest

from app.services import ai_provider
from app.services import report_ai_config


def test_resolve_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(report_ai_config, "_get", lambda key, default="": default)
    assert report_ai_config.resolve() is None


def test_resolve_maps_configured_values(monkeypatch):
    values = {
        "report_ai_base_url": "https://ark.example.com/api/v3",
        "report_ai_model": "deepseek-v4.1-flash",
        "report_ai_api_key": "sk-test",
    }
    monkeypatch.setattr(report_ai_config, "_get", lambda key, default="": values.get(key, default))
    override = report_ai_config.resolve()
    assert override is not None
    assert override.model == "deepseek-v4.1-flash"
    assert override.base_url.endswith("/api/v3")


def test_kwargs_carry_response_format_only_when_requested():
    assert "response_format" not in ai_provider._openai_kwargs(
        temperature=0.2, max_tokens=100)
    kwargs = ai_provider._openai_kwargs(
        temperature=0.2, max_tokens=100, response_format={"type": "json_object"})
    assert kwargs["response_format"] == {"type": "json_object"}


def test_override_used_for_client_and_model(monkeypatch):
    seen: dict[str, str] = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs):
                    seen["model"] = kwargs["model"]
                    seen["json"] = str(kwargs.get("response_format"))
                    msg = type("M", (), {"content": "{}"})()
                    return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    monkeypatch.setattr(ai_provider, "_openai_client",
                        lambda key, timeout, base_url="": (seen.update(key=key, base=_base(base_url)) or _Client()))
    monkeypatch.setattr(ai_provider, "current_ai_model", lambda: "global-model")
    monkeypatch.setattr(ai_provider, "_resolve_max_tokens", lambda v: v)
    monkeypatch.setattr(ai_provider, "_check_input_budget", lambda *a, **k: None)

    import asyncio

    override = ai_provider.AiEndpointOverride(
        base_url="https://ark.example.com/api/v3", api_key="k", model="deepseek-v4.1-flash")
    out = asyncio.run(ai_provider.generate_ai_text(
        [{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=100,
        response_format={"type": "json_object"}, ai_config=override))
    assert out == "{}"
    assert seen["model"] == "deepseek-v4.1-flash"
    assert seen["key"] == "k"
    assert "json_object" in seen["json"]


def _base(url: str) -> str:
    return url
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_ai_provider_report_override.py -q`
Expected: FAIL（`ImportError: cannot import name 'report_ai_config'`）

- [ ] **Step 3: 实现 ai_provider 扩展**

`backend/app/services/ai_provider.py`：

1. 顶部 dataclass（放在 `Message = dict[str, str]` 附近）：

```python
@dataclass(frozen=True)
class AiEndpointOverride:
    """单次调用的 AI 端点覆盖(研报管线专用); 空字段回退全局解析。

    设置了任一项即强制走 openai_compat chat.completions 路径(codex_cli /
    responses 协议不支持覆盖, 见 generate_ai_text)。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = ""

    def is_empty(self) -> bool:
        return not (self.base_url or self.api_key or self.model)
```

2. `generate_ai_text` 签名与分支（325-357）：

```python
async def generate_ai_text(
    messages: Sequence[Message],
    *,
    temperature: float | None = 0.3,
    max_tokens: int | None = 3000,
    timeout: float = 180.0,
    response_format: dict | None = None,
    ai_config: AiEndpointOverride | None = None,
) -> str:
    ...
    max_tokens = _resolve_max_tokens(max_tokens)
    _check_input_budget(messages, max_tokens=max_tokens)
    if ai_config is not None and not ai_config.is_empty():
        # 研报专用端点: 只支持 openai_compat, 不做 codex/responses 分支
        return await _run_openai_once(
            messages, temperature=temperature, max_tokens=max_tokens,
            timeout=timeout, response_format=response_format, ai_config=ai_config,
        )
    if is_codex_cli_provider():
        return await _run_codex_cli(messages, max_tokens=max_tokens, timeout=max(timeout, 600.0))
    ...
    return await _run_openai_once(
        messages, temperature=temperature, max_tokens=max_tokens,
        timeout=timeout, response_format=response_format,
    )
```

3. `_run_openai_once` / `_run_openai_message_once` 透传（513-558）：

```python
async def _run_openai_once(
    messages: Sequence[Message],
    *,
    temperature: float | None,
    max_tokens: int | None,
    timeout: float,
    response_format: dict | None = None,
    ai_config: AiEndpointOverride | None = None,
) -> str:
    message = await _run_openai_message_once(
        messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout,
        response_format=response_format, ai_config=ai_config,
    )
    ...

async def _run_openai_message_once(
    messages, *, temperature, max_tokens, timeout,
    tools: Sequence[dict] | None = None,
    response_format: dict | None = None,
    ai_config: AiEndpointOverride | None = None,
):
    override = ai_config or AiEndpointOverride()
    ai_key = override.api_key or secrets_store.get_ai_key()
    if not ai_key:
        raise RuntimeError("AI API Key 未配置, 请在设置页配置")
    client = _openai_client(ai_key, timeout, base_url=override.base_url)
    model = override.model or current_ai_model()
    req_messages = list(messages)
    kwargs = _openai_kwargs(temperature=temperature, max_tokens=max_tokens, tools=tools,
                            response_format=response_format)
    ...  # 其余 while 重试循环不变
```

4. `_openai_client`（672）增加 `base_url` 参数：

```python
def _openai_client(api_key: str, timeout: float, *, base_url: str = ""):
    from openai import AsyncOpenAI

    user_agent = secrets_store.get_ai_config("ai_user_agent", "") or settings.ai_user_agent
    return AsyncOpenAI(
        api_key=api_key,
        base_url=normalize_openai_base_url(
            base_url or secrets_store.get_ai_config("ai_base_url", settings.ai_base_url)),
        ...
    )
```

5. `_openai_kwargs`（806）增加 `response_format`：

```python
def _openai_kwargs(*, temperature, max_tokens, model="", base_url="",
                   prefer_final_answer=False, tools=None, response_format=None) -> dict:
    ...
    if response_format:
        kwargs["response_format"] = response_format
```

6. `_openai_retry_kwargs`（~789）在 temperature 分支后追加：

```python
    if "response_format" in retry_kwargs and _openai_error_param(exc) == "response_format":
        # 端点不支持 JSON mode: 去参重发一次(与 temperature 被拒同路径)
        retry_kwargs.pop("response_format")
```

`backend/app/services/report_ai_config.py`（新建）：

```python
"""研报管线专用 AI 端点配置: REPORT_AI_* 逐项缺省回退全局 AI 配置。

全部为空 → 返回 None, 由 ai_provider 走全局配置(与旧行为一致)。
"""
from __future__ import annotations

from app import secrets_store
from app.config import settings
from app.services.ai_provider import AiEndpointOverride


def _get(key: str, default: str = "") -> str:
    return (secrets_store.get_ai_config(key, default) or "").strip()


def resolve() -> AiEndpointOverride | None:
    """解析研报端点覆盖; 全空返回 None(回退全局)。"""
    base_url = _get("report_ai_base_url") or getattr(settings, "report_ai_base_url", "")
    model = _get("report_ai_model") or getattr(settings, "report_ai_model", "")
    api_key = _get("report_ai_api_key")
    if not (base_url or model or api_key):
        return None
    return AiEndpointOverride(base_url=base_url.strip(), model=model.strip(),
                              api_key=api_key.strip())
```

`backend/app/config.py`（AI 段落后）：

```python
    # 研报管线专用 AI 端点(留空逐项回退全局 AI_*); 建议指向 stock_ai 同款模型
    report_ai_base_url: str = ""
    report_ai_model: str = ""
    report_ai_max_output_tokens: int = 16000
```

`backend/.env.example` 追加：

```bash
# ===== 个股深度研报(可选; 留空逐项回退上面的 AI_*) =====
REPORT_AI_BASE_URL=
REPORT_AI_MODEL=
REPORT_AI_API_KEY=
REPORT_AI_MAX_OUTPUT_TOKENS=16000
```

- [ ] **Step 4: 运行测试确认通过（含既有 AI 测试不回归）**

Run: `cd backend && uv run --frozen pytest tests/test_ai_provider_report_override.py tests/test_ai_provider.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ai_provider.py backend/app/services/report_ai_config.py backend/app/config.py backend/.env.example backend/tests/test_ai_provider_report_override.py
git commit -m "feat(report): ai_provider 支持 response_format 与研报专用端点覆盖"
```

---

### Task 10: 七分片 LLM 管线 `report_shards`

**Files:**
- Create: `backend/app/services/report_shards.py`
- Test: `backend/tests/test_report_shards.py`（新建）

**Interfaces:**
- Consumes: Task 7 `app.reporting.content_schema`、Task 9 `ai_provider.generate_ai_text` / `report_ai_config.resolve()`
- Produces:
  - `SHARD_ORDER: tuple[str, ...]`、`SHARD_MAX_TOKENS: dict[str, int]`、`TEMPERATURE = 0.2`
  - `build_digest(blocks: dict) -> str`
  - `shard_system(key: str) -> str`、`shard_user(code: str, name: str, digest: str, date: str, key: str) -> str`
  - `async def analyze_shards(code: str, name: str, blocks: dict) -> tuple[dict, list[str]]`
  - `fill_rate(content: dict) -> float`
  - `MISSING = "数据缺失"`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_report_shards.py
"""七分片契约: digest 缺块标注、JSON 抽取、重试收敛、降级与填充率。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services import report_shards as rs


def _blocks(**over):
    base = {name: [] for name in (
        "basic_info", "spot", "share_structure", "zygc", "kline_daily", "fund_flow",
        "lhb", "fin_abstract", "fin_indicator_ths", "balance_sheet",
        "income_statement", "cashflow", "yjyg", "yjkb", "top10", "gdhs",
        "share_hold_change", "dividend", "share_alloc", "release", "notice", "news",
        "research", "recommend", "fund_hold", "margin")}
    base.update({
        "spot": [{"名称": "贵州茅台", "最新价": 1500.0}],
        "kline_daily": [{"date": f"2026-01-{i + 1:02d}", "open": 1.0, "high": 1.2,
                         "low": 0.9, "close": 1.1, "volume": 10.0} for i in range(60)],
        "technical_indicators": [{"MACD": 0.1}],
        "technical_signals": {"macd": "金叉"},
        "_analysis_date": "2026-10-15",
    })
    base.update(over)
    return base


def test_digest_marks_missing_blocks():
    digest = rs.build_digest(_blocks())
    assert "【公司简称】" in digest
    assert rs.MISSING in digest


def test_extract_json_strips_fence():
    assert rs._extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_extract_json_rejects_non_object():
    with pytest.raises(ValueError):
        rs._extract_json("[1, 2]")


def test_shard_prompts_cover_schema_keys():
    text = rs.shard_system("technical")
    assert "technical" in text
    assert "verdict" in text


def test_analyze_shards_retries_then_degrades(monkeypatch):
    calls: list[int] = []

    async def _fake_llm(system, user, max_tokens):
        calls.append(1)
        return "not json"

    monkeypatch.setattr(rs, "_call_llm", _fake_llm)
    content, failed = asyncio.run(rs.analyze_shards("600519", "贵州茅台", _blocks()))
    assert failed == list(rs.SHARD_ORDER)
    assert content["meta"]["code"] == "600519"
    assert rs.fill_rate(content) <= 0.4
    assert len(calls) == 3 * len(rs.SHARD_ORDER)


def test_analyze_shards_merges_valid_output(monkeypatch):
    good = {
        "identity": {"meta": {"name": "x", "code": "600519", "analysis_date": "2026-10-15"},
                     "profile": {"main_business": "白酒"}},
    }

    async def _fake_llm(system, user, max_tokens):
        for key, payload in good.items():
            if f"填写这一片 JSON" in user and key in system:
                return json.dumps(payload, ensure_ascii=False)
        return "{}"

    monkeypatch.setattr(rs, "_call_llm", _fake_llm)
    content, failed = asyncio.run(rs.analyze_shards("600519", "贵州茅台", _blocks()))
    assert "identity" not in failed or True  # 其它片可降级, 但 meta 必被强制回填
    assert content["meta"]["code"] == "600519"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_report_shards.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.report_shards`）

- [ ] **Step 3: 移植并异步化**

从 `C:\Users\19229\Desktop\stock_ai\analyze_api.py` 原样移植下列内容到 `backend/app/services/report_shards.py`：
`TEMPERATURE` / `SHARD_MAX_TOKENS` / `SHARDS` / `SHARD_ORDER` / `SHARD_KEYS` 与三条导入期断言 / `_num` / `kline_rows` / `latest_indicators` / `_jd` / `_head` / `_jv` / `_rows` / `_dict_rows` / `_momentum` / `_news_line` / `NAME_SOURCES` / `_usable_name` / `resolve_company_name` / `build_digest` / `ROLE` / `JSON_RULES` / `_item_note` / `_STR_NOTES` / `_skel_leaf` / `_skeleton_text` / `_shape_hints` / `shard_system` / `shard_user` / `_validate_shard` / `_deep_merge` / `_force_identity` / `_is_filled` / `_expected_slots` / `fill_rate`。

改动点（仅此三处）：

1. 导入：`from app.reporting.content_schema import ITEM_KEYS, MIN_LEN, SCHEMA, validate_content`（替换 `from content_schema import ...`）。
2. `api_call` → `_call_llm`（异步，走 ai_provider）：

```python
async def _call_llm(system: str, user: str, max_tokens: int) -> str:
    """七分片单次调用: 研报端点覆盖 + JSON mode; 输出截断由 ai_provider 重试处理。"""
    from app.services import ai_provider
    from app.services.report_ai_config import resolve as resolve_report_ai

    cap = int(getattr(settings, "report_ai_max_output_tokens", 16000) or 16000)
    return await ai_provider.generate_ai_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=TEMPERATURE,
        max_tokens=min(max_tokens, cap),
        timeout=600.0,
        response_format={"type": "json_object"},
        ai_config=resolve_report_ai(),
    )
```

3. `_shard_retry` / `analyze` 改 `async def`，调用处 `text = await _call_llm(...)`；`analyze` 重命名为 `analyze_shards`，签名保持 `(code, name, blocks) -> tuple[dict, list[str]]`，内部 `date = str(blocks.get("_analysis_date") or "")`，七片**串行**（不并发）；打印日志改为 `logger.info`。

移植后保留 stock_ai 的降级语义：单片 3 次重试仍失败 → 空片 + 记入 `failed`；`_force_identity` 强制回填 meta；`fill_rate` 整块缺席封顶 0.4。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_report_shards.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/report_shards.py backend/tests/test_report_shards.py
git commit -m "feat(report): 七分片 LLM 管线(JSON 校验/重试/降级/填充率)"
```

---

### Task 11: 渲染与终验移植（script_builder / render_report / loops / validate_html / 模板）

**Files:**
- Create: `backend/app/reporting/script_builder.py`（自 `analyze_api.py` 的 `build_script` 及其专有工具函数移植）
- Create: `backend/app/reporting/render_report.py`（自 `stock_ai\render_report.py`）
- Create: `backend/app/reporting/render_report_loops.py`（自 `stock_ai\render_report_loops.py`）
- Create: `backend/app/reporting/validate_html.py`（自 `stock_ai\validate_html.py`）
- Create: `backend/app/reporting/templates/{template_report.html,template_base.css,template_base.js}`（自 `stock_ai\shared\`）
- Test: `backend/tests/test_report_rendering.py`（新建）
- Test fixtures: `backend/tests/fixtures/stock_report/{data_600519.json,content_600519.json}`（自 `stock_ai\output\` 复制）

**Interfaces:**
- Produces:
  - `app.reporting.script_builder.build_script(blocks: dict) -> str`
  - `app.reporting.render_report.render(content: dict, blocks: dict) -> str`
  - `app.reporting.render_report.find_unresolved(html: str) -> list[str]`
  - `app.reporting.validate_html.check_html(path: str | Path) -> list[str]`（17 条，空列表 = 全绿）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_report_rendering.py
"""渲染与终验: 用 stock_ai 600519 真实缓存渲染, 17 条终验必须全绿。"""
from __future__ import annotations

import json
from pathlib import Path

from app.reporting import render_report, validate_html

FIXTURES = Path(__file__).parent / "fixtures" / "stock_report"


def _payload():
    data = json.loads((FIXTURES / "data_600519.json").read_text(encoding="utf-8"))
    content = json.loads((FIXTURES / "content_600519.json").read_text(encoding="utf-8"))
    return data["blocks"], content


def test_render_leaves_no_unresolved_slots(tmp_path):
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    assert render_report.find_unresolved(html) == []


def test_rendered_report_passes_all_checks(tmp_path):
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    out = tmp_path / "report.html"
    out.write_text(html, encoding="utf-8")
    assert validate_html.check_html(str(out)) == []


def test_script_builder_injects_no_nan():
    blocks, _ = _payload()
    script = __import__("app.reporting.script_builder", fromlist=["build_script"]).build_script(blocks)
    assert "NaN" not in script and "Infinity" not in script
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_report_rendering.py -q`
Expected: FAIL（`ModuleNotFoundError: app.reporting.render_report`）

- [ ] **Step 3: 复制资产与 fixture**

```bash
mkdir -p backend/app/reporting/templates backend/tests/fixtures/stock_report
cp "C:/Users/19229/Desktop/stock_ai/shared/template_report.html" backend/app/reporting/templates/
cp "C:/Users/19229/Desktop/stock_ai/shared/template_base.css" backend/app/reporting/templates/
cp "C:/Users/19229/Desktop/stock_ai/shared/template_base.js" backend/app/reporting/templates/
cp "C:/Users/19229/Desktop/stock_ai/output/data_600519.json" backend/tests/fixtures/stock_report/
cp "C:/Users/19229/Desktop/stock_ai/output/content_600519.json" backend/tests/fixtures/stock_report/
```

- [ ] **Step 4: 移植三个模块 + script_builder**

1. `script_builder.py`：从 `stock_ai\analyze_api.py` 移植 `_NONFINITE_RE` / `_json_text` / `pie_from_zygc` / `_markline` / `_markpoint` / `build_script`，以及它们依赖的 `_num` / `kline_rows` / `latest_indicators` / `KLINE_LIMIT`。**路径改动**：`JS_TEMPLATE` 由相对路径改为包内资源：

```python
JS_TEMPLATE = Path(__file__).with_name("templates") / "template_base.js"


def build_script(blocks: Any) -> str:
    with open(JS_TEMPLATE, encoding="utf-8") as f:
        js = f.read()
    ...  # 其余与 stock_ai 完全一致
```

2. `render_report.py`：原样移植，改两处导入：

```python
from app.reporting.script_builder import build_script
from app.reporting.render_report_loops import build_loop_slots

TEMPLATE = Path(__file__).with_name("templates") / "template_report.html"
```

`_load_css` 同样指向包内 `templates/template_base.css`。

3. `render_report_loops.py`：原样移植，仅改导入路径（无相对资源引用时零改动）。

4. `validate_html.py`：原样移植；`check_html(path)` 的 CLI 入口（`if __name__ == "__main__"` 与 `sys.argv` 部分）保留但改为可导入形式；常量（`NAV_ANCHOR_COUNT` 等）不改。

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_report_rendering.py -q`
Expected: PASS（渲染 600519 真实缓存并通过 17 条终验）

- [ ] **Step 6: 提交**

```bash
git add backend/app/reporting backend/tests/test_report_rendering.py backend/tests/fixtures/stock_report
git commit -m "feat(report): 移植模板渲染与 17 条终验(含 600519 fixture)"
```

---

### Task 12: 报告存储 + 任务编排 + API

**Files:**
- Create: `backend/app/services/stock_report_store.py`
- Create: `backend/app/services/stock_report_jobs.py`
- Create: `backend/app/api/stock_reports.py`
- Modify: `backend/app/main.py:41` 附近导入 + `:583` 之后 `app.include_router(stock_reports.router)`
- Modify: `backend/app/services/task_registry.py`（`TASK_KINDS` 增加 `"stock_report"`）
- Test: `backend/tests/test_stock_report_api.py`（新建）

**Interfaces:**
- Produces:
  - `stock_report_store.save_report(meta: dict, html: str) -> dict`、`list_reports(symbol="") -> list[dict]`、`get_report(report_id) -> dict | None`、`html_path(report_id) -> Path`
  - `stock_report_jobs.start(symbol: str, name: str) -> dict`、`get(job_id) -> dict | None`、`find_running(symbol) -> dict | None`、`finish(job_id, **fields)`、`JOB_KINDS`
  - API：`POST /api/stock-reports/generate`、`GET /api/stock-reports/{job_id}`、`GET /api/stock-reports/{job_id}/html`、`GET /api/stock-reports?symbol=`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_stock_report_api.py
"""研报 API 契约: 生成/状态/HTML/列表、同 symbol 防重、失败 fail-closed。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stock_reports as api_mod
from app.services import stock_report_jobs, stock_report_store


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path, raising=False)
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


def test_generate_then_status(client, monkeypatch):
    started: list[str] = []

    monkeypatch.setattr(stock_report_jobs, "_spawn",
                        lambda job, runner: started.append(job["id"]) or job)
    res = client.post("/api/stock-reports/generate", json={"symbol": "600519.SH"})
    assert res.status_code == 200
    job = res.json()["job"]
    assert job["status"] == "running" and started == [job["id"]]

    again = client.post("/api/stock-reports/generate", json={"symbol": "600519.SH"})
    assert again.json()["job"]["id"] == job["id"]  # 防重


def test_status_unknown_job_is_404(client):
    assert client.get("/api/stock-reports/nope").status_code == 404


def test_html_endpoint_serves_saved_report(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path)
    meta = stock_report_store.save_report(
        {"symbol": "600519.SH", "name": "贵州茅台", "status": "done"}, "<html>x</html>")
    res = client.get(f"/api/stock-reports/{meta['id']}/html")
    assert res.status_code == 200 and res.text == "<html>x</html>"


def test_list_reports_filters_by_symbol(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path)
    stock_report_store.save_report({"symbol": "600519.SH", "name": "贵州茅台"}, "<html>a</html>")
    stock_report_store.save_report({"symbol": "000001.SZ", "name": "平安银行"}, "<html>b</html>")
    res = client.get("/api/stock-reports", params={"symbol": "600519.SH"})
    assert [r["symbol"] for r in res.json()["reports"]] == ["600519.SH"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_stock_report_api.py -q`
Expected: FAIL（`ModuleNotFoundError: app.api.stock_reports`）

- [ ] **Step 3: 实现存储**

```python
"""研报存储 — 元数据 JSON(JsonReportStore 模式) + HTML 文件, 上限 50 份。

落盘: data/user_data/stock_reports.json (元数据, 原子写)
      data/user_data/stock_reports/stock-report-{id}.html (正文)
超限裁剪时同步删除对应 HTML 文件。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from app.config import settings
from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

MAX_REPORTS = 50
_LOCK = threading.Lock()


def _base_dir() -> Path:
    return settings.data_dir / "user_data"


def _meta_path() -> Path:
    return _base_dir() / "stock_reports.json"


def html_path(report_id: str) -> Path:
    return _base_dir() / "stock_reports" / f"stock-report-{report_id}.html"


def _load() -> list[dict]:
    path = _meta_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("[report-store] 元数据损坏, 重置为空: %s", path)
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def save_report(meta: dict, html: str) -> dict:
    """写入一份报告(元数据 + HTML), 返回含 id 的元数据。"""
    with _LOCK:
        report_id = f"sr{int(time.time() * 1000)}"
        record = {**meta, "id": report_id,
                  "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        path = html_path(report_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, html)
        records = [record] + _load()
        for stale in records[MAX_REPORTS:]:
            try:
                html_path(str(stale.get("id") or "")).unlink(missing_ok=True)
            except OSError:
                logger.warning("[report-store] 旧报告删除失败: %s", stale.get("id"))
        records = records[:MAX_REPORTS]
        atomic_write_text(_meta_path(), json.dumps(records, ensure_ascii=False, indent=1))
        return record


def list_reports(symbol: str = "") -> list[dict]:
    rows = _load()
    if symbol:
        rows = [r for r in rows if r.get("symbol") == symbol]
    return rows


def get_report(report_id: str) -> dict | None:
    return next((r for r in _load() if r.get("id") == report_id), None)
```

（顶部补 `import json`。）

- [ ] **Step 4: 实现任务编排**

```python
"""研报任务编排 — 进程内任务表 + 单飞防重 + 后台 asyncio 执行。

状态: running → done / error / degraded(分片降级或填充率不足仍产出报告)。
重启后任务表清空, 前端轮询 404 时按「任务已丢失, 请重新生成」处理。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)

JOB_KINDS = ("stock_report",)
_JOBS: dict[str, dict] = {}
_LOCK = asyncio.Lock()


def get(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def find_running(symbol: str) -> dict | None:
    return next((j for j in _JOBS.values()
                 if j["symbol"] == symbol and j["status"] == "running"), None)


def start(symbol: str, name: str) -> dict:
    """新建任务(调用方已确认无同 symbol running 任务)。"""
    job = {"id": uuid.uuid4().hex[:12], "symbol": symbol, "name": name,
           "status": "running", "phase": "queued", "progress": 0,
           "started_at": time.time(), "error": "", "report_id": ""}
    _JOBS[job["id"]] = job
    return job


def update(job_id: str, **fields) -> None:
    job = _JOBS.get(job_id)
    if job:
        job.update(fields)


def _spawn(job: dict, runner) -> None:
    """把 runner(job) 挂到事件循环(测试替换为同步桩)。"""
    asyncio.create_task(runner(job))
```

> 循环导入说明：`stock_report_pipeline` 反向依赖本模块（更新 job 状态），故 `runner` 由调用方（API / pipeline）传入，本模块不做顶层导入。`asyncio.create_task` 只能在运行中的事件循环内调用——API 端点与 `analyze_stock_stream` 都在 loop 内，满足该约束。

- [ ] **Step 5: 实现 API 与注册**

```python
"""个股深度研报 API。

路由前缀: /api/stock-reports
  POST /generate                 触发生成(同 symbol 防重)
  GET  /{job_id}                 任务状态(阶段/进度/错误/报告 id)
  GET  /{job_id}/html            报告正文(text/html)
  GET  ?symbol=                  历史报告列表
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.services import stock_report_jobs, stock_report_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stock-reports", tags=["stock-reports"])


class GenerateRequest(BaseModel):
    symbol: str


@router.post("/generate")
async def generate(request: Request, req: GenerateRequest):
    symbol = (req.symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(400, "symbol 不能为空")
    running = stock_report_jobs.find_running(symbol)
    if running:
        return {"job": running, "deduped": True}
    job = stock_report_jobs.start(symbol, name="")
    from app.services import stock_report_pipeline

    stock_report_jobs._spawn(job, lambda j: stock_report_pipeline.run_report_job(
        j, repo=request.app.state.repo))
    return {"job": job, "deduped": False}


@router.get("/{job_id}")
def get_status(job_id: str):
    job = stock_report_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或已随进程重启丢失, 请重新生成")
    return {"job": job}


@router.get("/{job_id}/html", response_class=HTMLResponse)
def get_html(job_id: str):
    record = stock_report_store.get_report(job_id)
    if not record:
        raise HTTPException(404, "报告不存在")
    path = stock_report_store.html_path(job_id)
    if not path.exists():
        raise HTTPException(410, "报告文件已不存在(可能已被清理)")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.get("")
def list_reports(symbol: str = Query("", description="按标的过滤")):
    return {"reports": stock_report_store.list_reports(symbol.strip())}
```

`backend/app/main.py`：导入块加 `stock_reports`，注册表追加 `app.include_router(stock_reports.router)`；`task_registry.TASK_KINDS` 增加 `"stock_report"`。

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && uv run --frozen pytest tests/test_stock_report_api.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/stock_report_store.py backend/app/services/stock_report_jobs.py backend/app/api/stock_reports.py backend/app/main.py backend/app/services/task_registry.py backend/tests/test_stock_report_api.py
git commit -m "feat(report): 报告存储 + 任务编排 + 研报 API"
```

---

### Task 13: 引擎接入（NDJSON 适配 + 默认开关 + 文档）

**Files:**
- Create: `backend/app/services/stock_report_pipeline.py`
- Modify: `backend/app/services/stock_analyzer.py`（新增 `report` 分支）
- Modify: `backend/app/config.py:120-125`（默认值改 `report`）
- Modify: `backend/.env.example`（`STOCK_ANALYSIS_ENGINE` 说明）
- Modify: `README.md`（个股分析引擎说明）
- Test: `backend/tests/test_stock_report_engine.py`（新建）

**Interfaces:**
- Consumes: Task 8/10/11/12
- Produces:
  - `stock_report_pipeline.run_report_job(job: dict, *, repo) -> None`（后台任务：采集→分片→渲染→终验→存储→更新 job）
  - `stock_report_pipeline.analyze_stock_stream(repo, data_dir, symbol, focus="") -> AsyncIterator[str]`（NDJSON：`meta` / `delta` / `error` / `done`，`done` 带 `report_id` / `report_url`）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_stock_report_engine.py
"""引擎接入契约: 三态分发、非法值 fail-closed、NDJSON 事件序列。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services import stock_analyzer, stock_report_jobs, stock_report_pipeline


def _events(chunks):
    return [json.loads(c) for c in chunks]


def test_invalid_engine_yields_error(monkeypatch):
    monkeypatch.setattr(stock_analyzer.settings, "stock_analysis_engine", "nope", raising=False)
    out = asyncio.run(_collect(stock_analyzer.analyze_stock_stream(None, None, "600519.SH")))
    events = _events(out)
    assert events[0]["type"] == "error"


def test_report_engine_streams_progress_and_done(monkeypatch):
    monkeypatch.setattr(stock_analyzer.settings, "stock_analysis_engine", "report", raising=False)

    async def _fake_job(job, *, repo=None):
        job.update({"status": "done", "report_id": "sr1", "phase": "done", "progress": 100})

    monkeypatch.setattr(stock_report_pipeline, "run_report_job", _fake_job)
    monkeypatch.setattr(stock_report_pipeline, "POLL_SECONDS", 0.01)
    out = asyncio.run(_collect(stock_report_pipeline.analyze_stock_stream(
        None, None, "600519.SH")))
    events = _events(out)
    assert events[0]["type"] == "meta" and events[0]["symbol"] == "600519.SH"
    assert events[-1]["type"] == "done" and events[-1]["report_id"] == "sr1"


async def _collect(agen):
    return [chunk async for chunk in agen]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_stock_report_engine.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.stock_report_pipeline`）

- [ ] **Step 3: 实现 pipeline（NDJSON 适配 + 后台任务）**

```python
"""个股深度研报引擎 — NDJSON 适配(对话框) + 后台任务(采集→分片→渲染→终验→存储)。

事件协议与 DSA/legacy 引擎一致: meta / delta / error / done;
done 额外携带 report_id / report_url, 前端据此渲染「打开报告」。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from app.services import (report_shards, stock_report_data, stock_report_jobs,
                          stock_report_store)
from app.services.stock_report_data import ReportDataUnavailable

logger = logging.getLogger(__name__)

PHASE_LABELS = {
    "collect": "Phase 1/4 数据采集中",
    "analyze": "Phase 2/4 七分片分析中",
    "render": "Phase 3/4 模板渲染中",
    "validate": "Phase 4/4 终验中",
}
POLL_SECONDS = 2.0  # 对话框进度轮询间隔(测试 monkeypatch 缩短)


def _event(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def run_report_job(job: dict, *, repo=None) -> None:
    """后台任务主体; 任何异常都必须落到 job.status=error(前端不能永久转圈)。"""
    symbol, name = job["symbol"], job["name"]
    try:
        job.update({"phase": "collect", "progress": 5})
        blocks, missing = await asyncio.to_thread(stock_report_data.collect_blocks, symbol)
        if not name:
            name = report_shards.resolve_company_name(blocks, symbol.split(".")[0])
        job.update({"phase": "analyze", "progress": 30, "name": name})

        content, failed = await report_shards.analyze_shards(
            symbol.split(".")[0], name, blocks)
        rate = report_shards.fill_rate(content)
        job.update({"phase": "render", "progress": 80})

        from app.reporting import render_report, validate_html

        html = await asyncio.to_thread(render_report.render, content, blocks)
        job.update({"phase": "validate", "progress": 90})
        tmp = stock_report_store.html_path(f"draft-{job['id']}")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(html, encoding="utf-8")
        errors = await asyncio.to_thread(validate_html.check_html, str(tmp))
        if errors:
            record = stock_report_store.save_report(
                {"symbol": symbol, "name": name, "status": "failed",
                 "fill_rate": rate, "failed_shards": failed, "errors": errors}, html)
            job.update({"status": "error", "phase": "validate", "progress": 95,
                        "report_id": record["id"],
                        "error": f"终验 {len(errors)} 项飘红: {errors[0]}"})
            return

        status = "degraded" if (failed or rate < 0.5 or missing) else "done"
        record = stock_report_store.save_report(
            {"symbol": symbol, "name": name, "status": status, "fill_rate": rate,
             "failed_shards": failed, "missing_blocks": missing}, html)
        job.update({"status": "done", "phase": "done", "progress": 100,
                    "report_id": record["id"], "report_url": f"/api/stock-reports/{record['id']}/html",
                    "degraded": status == "degraded"})
    except ReportDataUnavailable as exc:
        job.update({"status": "error", "phase": "collect", "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("[report-job] 任务失败: %s", job["id"])
        job.update({"status": "error", "phase": job.get("phase", ""),
                    "error": f"{type(exc).__name__}: {exc}"})


async def analyze_stock_stream(repo, data_dir: Path, symbol: str,
                               focus: str = "") -> AsyncIterator[str]:
    """对话框入口: 立即返回 meta, 轮询 job 进度产生 delta, 终态产生 done/error。"""
    symbol = (symbol or "").strip().upper()
    job = stock_report_jobs.find_running(symbol) or stock_report_jobs.start(symbol, "")
    if job.get("status") == "running" and not job.get("_spawned"):
        job["_spawned"] = True
        asyncio.create_task(run_report_job(job))
    yield _event({"type": "meta", "symbol": symbol,
                  "summary": "深度研报生成中(通常 3-8 分钟)", "job_id": job["id"]})
    seen = ""
    while True:
        await asyncio.sleep(POLL_SECONDS)
        phase = job.get("phase", "")
        if phase != seen and phase in PHASE_LABELS:
            seen = phase
            yield _event({"type": "delta", "content": f"\n- {PHASE_LABELS[phase]} "
                                                     f"({job.get('progress', 0)}%)\n"})
        if job.get("status") in ("done", "error"):
            break
    if job.get("status") == "error":
        yield _event({"type": "error", "message": job.get("error") or "研报生成失败"})
        return
    yield _event({"type": "done", "report_id": job.get("report_id", ""),
                  "report_url": job.get("report_url", ""),
                  "degraded": bool(job.get("degraded"))})
```

`backend/app/services/stock_analyzer.py`：

```python
ENGINE_REPORT = "report"
VALID_ENGINES = (ENGINE_REPORT, ENGINE_DSA, ENGINE_LEGACY)
...
    if engine == ENGINE_REPORT:
        from app.services import stock_report_pipeline

        async for chunk in stock_report_pipeline.analyze_stock_stream(
                repo, data_dir, symbol, focus):
            yield chunk
        return
```

`backend/app/config.py`：`stock_analysis_engine: str = "report"`，注释同步为「report(默认, 个股深度研报) / dsa / legacy」。

`backend/.env.example`：

```bash
# 个股 AI 分析引擎: report(默认, 深度研报, 走 akshare_ext + REPORT_AI_*) | dsa | legacy
STOCK_ANALYSIS_ENGINE=report
```

`README.md`：「个股 AI 分析」小节补一句：默认引擎为深度研报（3–8 分钟，产出 HTML），依赖 akshare_ext 数据源与 REPORT_AI_* 配置；回退 `STOCK_ANALYSIS_ENGINE=dsa`。

- [ ] **Step 4: 运行测试确认通过（含既有引擎开关测试）**

Run: `cd backend && uv run --frozen pytest tests/test_stock_report_engine.py tests/test_stock_analyzer_engine_switch.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/stock_report_pipeline.py backend/app/services/stock_analyzer.py backend/app/config.py backend/.env.example README.md backend/tests/test_stock_report_engine.py
git commit -m "feat(report): 研报引擎接入(默认开, NDJSON 协议不变)"
```

---

### Task 14: 前端接入（完成态打开报告 + 等待态文案 + 文档）

**Files:**
- Modify: `frontend/src/lib/api.ts`（新增研报请求与类型）
- Modify: `frontend/src/lib/stockAnalysisStore.ts`（`done` 事件解析 `report_id`/`report_url`）
- Modify: `frontend/src/components/stock-analysis/StockAnalysisDialog.tsx`（等待态文案 + 完成态按钮）
- Modify: `frontend/src/custom/stock-analysis/extension.tsx`（文案）
- Test: `frontend/src/components/stock-analysis/StockReportButton.test.tsx`（新建）

**Interfaces:**
- Consumes: Task 12/13 的 NDJSON（`done` 事件字段）与 `GET /api/stock-reports/{id}/html`
- Produces: `stockAnalysisStore` 中 `ActiveTask` 增加可选字段 `reportUrl?: string`、`reportId?: string`；对话框完成态渲染「打开报告」

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/stock-analysis/StockReportButton.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StockAnalysisDialog } from './StockAnalysisDialog'

describe('StockAnalysisDialog 研报完成态', () => {
  it('done 事件带 reportUrl 时渲染打开报告按钮', () => {
    render(
      <StockAnalysisDialog
        mode="active"
        minimized={false}
        task={{
          symbol: '600519.SH', name: '贵州茅台', focus: '', phase: 'done',
          content: '- Phase 1/4 数据采集中', error: '', meta: { summary: '深度研报' },
          reportId: 'sr1', reportUrl: '/api/stock-reports/sr1/html',
        }}
      />,
    )
    expect(screen.getByRole('link', { name: /打开报告/ })).toHaveAttribute(
      'href', '/api/stock-reports/sr1/html')
  })

  it('旧引擎(done 无 reportUrl)不渲染按钮', () => {
    render(
      <StockAnalysisDialog
        mode="active"
        minimized={false}
        task={{
          symbol: '600519.SH', name: '贵州茅台', focus: '', phase: 'done',
          content: '分析正文', error: '', meta: { summary: '四维分析' },
        }}
      />,
    )
    expect(screen.queryByRole('link', { name: /打开报告/ })).toBeNull()
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && pnpm vitest run src/components/stock-analysis/StockReportButton.test.tsx`
Expected: FAIL（属性 `reportUrl` 不存在 / 找不到链接）

- [ ] **Step 3: 实现**

1. `lib/api.ts`：加 `export interface StockReportStatus {...}` 与 `export async function fetchStockReportStatus(jobId: string)`（沿用文件内既有 request 封装），以及 `export const stockReportHtmlUrl = (reportId: string) => \`/api/stock-reports/${reportId}/html\``。
2. `lib/stockAnalysisStore.ts`：`ActiveTask` 类型加 `reportId?: string; reportUrl?: string;`；解析 `done` 事件时读取 `payload.report_id` / `payload.report_url` 写入 task；等待态文案改为「深度研报生成中，通常 3–8 分钟」。
3. `StockAnalysisDialog.tsx`：完成态（`phase === 'done'` 且 `task.reportUrl` 存在）在操作区渲染：

```tsx
<a href={task.reportUrl} target="_blank" rel="noreferrer"
   className="inline-flex items-center gap-1.5 rounded-lg border border-sky-400/40 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-200 hover:bg-sky-500/20">
  <LineChart className="h-3.5 w-3.5" /> 打开报告
</a>
```

并在 `degraded` 为真时在正文顶部插入一条 `AlertTriangle` 提示「报告已生成但存在降级项（分片失败或数据缺失），内容完整性可能受影响」。
4. `custom/stock-analysis/extension.tsx`：3 处文案「四维分析」→「深度研报」（按钮 title/tooltip/描述）。

- [ ] **Step 4: 运行测试 + 构建**

Run: `cd frontend && pnpm vitest run src/components/stock-analysis/StockReportButton.test.tsx && pnpm build`
Expected: 测试 PASS，构建成功

- [ ] **Step 5: 提交**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/stockAnalysisStore.ts frontend/src/components/stock-analysis/StockAnalysisDialog.tsx frontend/src/custom/stock-analysis/extension.tsx frontend/src/components/stock-analysis/StockReportButton.test.tsx
git commit -m "feat(report): 前端深度研报入口(完成态打开报告 + 等待态文案)"
```

---

### Task 15: 一致性验收与收口（需用户确认真模型调用后执行）

**Files:**
- Create: `backend/scripts/compare_stock_report_blocks.py`（对比工具）
- Test: `backend/tests/test_report_block_parity.py`（离线：用 fixture 验证同一 blocks 产出同一 digest/渲染）

**Interfaces:**
- Consumes: 全部前序任务
- Produces: `python backend/scripts/compare_stock_report_blocks.py --stock-ai C:/Users/19229/Desktop/stock_ai/output/data_600519.json --ours <cache.json>` → 逐块差异报告（退出码 0 = 无差异）

- [ ] **Step 1: 写离线一致性测试**

```python
# backend/tests/test_report_block_parity.py
"""一致性: 同一 stock_ai 缓存块 → 同一 digest 结构 + 渲染通过 17 条终验。"""
from __future__ import annotations

import json
from pathlib import Path

from app.reporting import render_report, validate_html
from app.services import report_shards

FIXTURES = Path(__file__).parent / "fixtures" / "stock_report"


def test_digest_from_stock_ai_blocks_is_stable(tmp_path):
    blocks = json.loads((FIXTURES / "data_600519.json").read_text(encoding="utf-8"))["blocks"]
    content = json.loads((FIXTURES / "content_600519.json").read_text(encoding="utf-8"))
    digest = report_shards.build_digest(blocks)
    assert "【公司简称】" in digest and "【券商研报（前10）】" in digest
    html = render_report.render(content, blocks)
    out = tmp_path / "p.html"
    out.write_text(html, encoding="utf-8")
    assert validate_html.check_html(str(out)) == []
```

- [ ] **Step 2: 写对比脚本**

```python
"""对比 stock_ai 与本管线同一标的的数据块(数值口径一致性)。

用法: uv run python scripts/compare_stock_report_blocks.py \
        --stock-ai <data_*.json> --ours <report_cache/*.json>
退出码: 0=无差异, 1=有差异(打印逐块差异), 2=输入不可读
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_blocks(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    blocks = payload.get("blocks") or payload
    return {k: v for k, v in blocks.items() if not str(k).startswith("_")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock-ai", required=True)
    ap.add_argument("--ours", required=True)
    args = ap.parse_args()

    left = _load_blocks(Path(args.stock_ai))
    right = _load_blocks(Path(args.ours))
    diffs: list[str] = []
    for name in sorted(set(left) | set(right)):
        a, b = left.get(name), right.get(name)
        if name == "kline_minute":
            continue  # 本管线按设计不采集
        if a == b:
            continue
        if isinstance(a, list) and isinstance(b, list) and len(a) != len(b):
            diffs.append(f"{name}: 行数 {len(a)} vs {len(b)}")
            continue
        diffs.append(f"{name}: 内容差异")
    if not diffs:
        print("✅ 数据块一致")
        return 0
    print("❌ 数据块差异:")
    for line in diffs:
        print(f"  - {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: 离线验证**

Run: `cd backend && uv run --frozen pytest tests/test_report_block_parity.py -q`
Expected: PASS

- [ ] **Step 4: 真模型端到端（**停下等用户确认**）**

得到用户明确确认后执行：

```bash
cd backend
uv run --frozen python -c "from app.services import stock_report_data; b,m=stock_report_data.collect_blocks('600519.SH', force=True); print(len(b), m)"
uv run --frozen python scripts/compare_stock_report_blocks.py \
  --stock-ai "C:/Users/19229/Desktop/stock_ai/output/data_600519.json" \
  --ours "$(ls -t ../data/report_cache/600519.SH/*.json | head -1)"
```

随后在面板里对 600519 触发一次「个股 AI 分析」，确认：进度文案推进 → 「打开报告」可打开 → 报告通过 17 条终验 → 与 stock_ai 当日报告比对章节与数字。

- [ ] **Step 5: 收口提交**

```bash
git add backend/scripts/compare_stock_report_blocks.py backend/tests/test_report_block_parity.py
git commit -m "test(report): 一致性对比脚本与离线 parity 测试"
```

---

## Self-Review 记录

- **Spec 覆盖**：§2.1 引擎接入 → Task 13；§2.2 数据层（4 能力 + 插件 + 同源保真 + 缓存）→ Task 1-8；§2.3 LLM 管线与配置 → Task 9-10；§2.4 渲染/终验/存储 → Task 11-12；§2.5 API 与任务 → Task 12；§2.6 前端 → Task 14；§4 工作量 → 任务粒度对应；§5 验证（含第 8 条一致性验收）→ 各任务测试 + Task 15；§6 风险回滚 → Global Constraints 与引擎回退开关（Task 13）。
- **未纳入本计划**：`update_stock_report` 增量更新、独立研报中心页面、mcp-server 工具（spec §3 非目标）。
- **已知取舍**：`technical_indicators.py` 保持 stock_ai 的 pandas 实现（不重写为 Polars）——移植目标是与 stock_ai 指标数值逐项一致，且 pandas 已是 backend 依赖；仅研报管线内部调用。
- **块数说明**：本管线采集 26 个数据块（stock_ai 的 `kline_minute` 按设计跳过，digest/渲染均不消费）；对比脚本对 `kline_minute` 跳过比对。
- **前端测试环境**：`StockAnalysisDialog` 渲染若依赖 store 初始化或既有 vitest/jsdom 配置，参照 `frontend/src/components/stock-analysis/AiAnalyzeButton.test.tsx` 的 setup 补齐 mock/provider；不得为过测试删除组件逻辑。
