"""DSA 问股 agent 桥接 — 在 TSP 进程内初始化 DSA Config 并构建 agent executor。

DSA (daily_stock_analysis) 的 ask agent 以 vendor 形式存在于另一个 checkout, 由
``DSA_VENDOR_DIR``(settings.dsa_vendor_dir) 指向其根目录(含 ``src/`` 与 ``data_provider/``);
本仓库不复制该目录, 详见 docs/superpowers/specs/2026-10-14-dsa-stock-agent-port-design.md。

职责:
  1. 把 vendor 根目录加入 sys.path (DSA 用绝对包名 src / data_provider)。
  2. 用 TSP 的 AI 配置 (secrets_store + settings) 构造 DSA Config, 并短路其单例
     (``Config._instance = cfg``), 彻底绕开 DSA 自身的 .env 读取与环境变量注入。
  3. 懒构建并缓存 ``build_agent_executor(cfg)``。
  4. ``reset()`` 供 TSP AI/数据配置变更时重建 DSA 运行时 (工具注册表一并重置)。
"""
from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# 已注入 sys.path 的 vendor 目录(配置变更时按新路径重新注入)。
_injected_vendor: Path | None = None

# DSA Config 模块级缓存。DSA 的 get_instance() 在 _instance 为空时会自动走
# _load_from_env()（读 .env / 注入环境变量），所以必须先于任何 get_instance()
# 调用直接设置 Config._instance，才能短路其环境读取。
_cfg = None


def _vendor_dir() -> Path:
    """解析 DSA vendor 根目录; 未配置或路径无效时给出可操作报错 (fail-closed)。"""
    from app.config import settings

    raw = str(getattr(settings, "dsa_vendor_dir", "") or "").strip()
    if not raw:
        raise ValueError(
            "未配置 DSA_VENDOR_DIR: DSA 问股 agent 需要指向 daily_stock_analysis vendor "
            "根目录(含 src/ 与 data_provider/); 也可设 STOCK_ANALYSIS_ENGINE=legacy "
            "回退到旧四维分析"
        )
    path = Path(raw).expanduser()
    if not (path / "src").is_dir():
        raise ValueError(f"DSA_VENDOR_DIR={path} 无效: 该目录下没有 src/")
    return path


def _ensure_path() -> None:
    global _injected_vendor

    vendor = _vendor_dir()
    if _injected_vendor is not None and _injected_vendor != vendor:
        # 配置切换: 移除旧路径, 避免同名包 src/data_provider 命中上一个 checkout。
        with contextlib.suppress(ValueError):
            sys.path.remove(str(_injected_vendor))
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    _injected_vendor = vendor


def _resolve_model() -> str:
    from app import secrets_store
    from app.config import settings

    model = (secrets_store.get_ai_config("ai_model", settings.ai_model) or "").strip()
    if not model:
        model = "deepseek-v4-flash"
    return model if "/" in model else f"openai/{model}"


def _search_keys(field: str, env_name: str) -> list:
    """取 DSA 搜索 Key 列表：secrets.json 优先，否则环境变量，逗号分隔。

    与 get_env_backed_secret 同优先级语义；返回 [] 表示未配置，
    对应搜索工具不可用（agent 会降级为无新闻模式）。
    """
    from app import secrets_store

    raw = secrets_store.get_env_backed_secret(field, env_name)
    return [k.strip() for k in raw.split(",") if k.strip()]


def build_dsa_config():
    """用 TSP 的 AI / 数据配置构造 DSA Config（短路其 .env 读取）。"""
    _ensure_path()
    from src.config import Config

    from app import secrets_store
    from app.config import settings

    model = _resolve_model()
    key = secrets_store.get_ai_key()
    base = (secrets_store.get_ai_config("ai_base_url", settings.ai_base_url) or "").strip()
    tickflow_key = secrets_store.get_tickflow_key()
    # 筹码分布等工具走 TushareFetcher（cyq_chips），与 TSP tushare 插件同源
    # （secrets.json 优先，否则 TUSHARE_TOKEN）；不传则筹码/部分行情工具直接空返回。
    tushare_token = secrets_store.get_env_backed_secret("tushare_api_key", "TUSHARE_TOKEN")
    # 情报搜索工具（search_stock_news / search_comprehensive_intel）要求至少一家
    # 搜索引擎有 Key，否则 SearchService.is_available 为 False，agent 报
    # “情报搜索工具暂不可用”。DSA 自身读 .env，但本桥短路了其单例，
    # 故必须在此显式透传（字段名与 DSA 的 *_API_KEYS 环境变量对齐）。
    anspire_keys = _search_keys("anspire_api_keys", "ANSPIRE_API_KEYS")
    bocha_keys = _search_keys("bocha_api_keys", "BOCHA_API_KEYS")
    minimax_keys = _search_keys("minimax_api_keys", "MINIMAX_API_KEYS")
    tavily_keys = _search_keys("tavily_api_keys", "TAVILY_API_KEYS")
    brave_keys = _search_keys("brave_api_keys", "BRAVE_API_KEYS")
    serpapi_keys = _search_keys("serpapi_keys", "SERPAPI_API_KEYS")
    searxng_urls = []
    for u in (
        secrets_store.get_env_backed_secret(
            "searxng_base_urls", "SEARXNG_BASE_URLS"
        ).split(",")
    ):
        u = u.strip()
        if not u:
            continue
        # 与 DSA 自身 _load_from_env 同口径：只要 http(s) + 有 host，非法直接忽略
        p = urlsplit(u)
        if p.scheme in ("http", "https") and p.netloc:
            searxng_urls.append(u)
        else:
            logger.warning("SEARXNG_BASE_URLS 忽略非法 URL: %s", u)

    cfg = Config(
        agent_mode=True,
        agent_arch="single",
        generation_backend="litellm",
        agent_generation_backend="litellm",
        litellm_model=model,
        agent_litellm_model=model,
        openai_api_keys=[key] if key else [],
        openai_base_url=base or None,
        llm_model_list=[],
        llm_temperature=0.5,
        agent_max_steps=12,
        # agent 每步要实时拉行情/筹码/板块数据 + 调模型，150s 预算实测不够
        # （冒烟时撞墙钟超时）；600s 为 DSA 默认值，足够完整决策仪表盘。
        agent_orchestrator_timeout_s=600,
        database_path=str(settings.data_dir / "dsa.db"),
        tickflow_api_key=tickflow_key or None,
        tushare_token=tushare_token or None,
        anspire_api_keys=anspire_keys,
        bocha_api_keys=bocha_keys,
        minimax_api_keys=minimax_keys,
        tavily_api_keys=tavily_keys,
        brave_api_keys=brave_keys,
        serpapi_keys=serpapi_keys,
        searxng_base_urls=searxng_urls,
        realtime_source_priority="tencent,akshare_sina,efinance,akshare_em",
    )
    return cfg


def get_dsa_config():
    """返回 DSA Config（模块级缓存；先于任何 get_instance() 直接短路其单例）。"""
    global _cfg
    _ensure_path()
    if _cfg is None:
        _cfg = build_dsa_config()
        from src.config import Config

        Config._instance = _cfg
    return _cfg


def get_agent_executor():
    """懒构建并缓存 DSA agent executor（问股决策仪表盘路径）。"""
    _ensure_path()
    from src.agent import factory as agent_factory

    cfg = get_dsa_config()
    return agent_factory.build_agent_executor(cfg)


def reset() -> None:
    """TSP AI/数据配置变更后重建 DSA 运行时（Config + 工具注册表 + 搜索单例）。"""
    global _cfg
    _ensure_path()
    from src.agent import factory as agent_factory
    from src.config import Config
    from src.search_service import reset_search_service

    _cfg = build_dsa_config()
    Config._instance = _cfg
    agent_factory.reset_tool_registry()
    # 搜索服务是独立单例（按首次构造时的 Key 快照建 provider），不清掉
    # 则换 Key 后仍沿用旧空 provider，搜索工具持续不可用。
    reset_search_service()
    logger.info("DSA agent runtime reset")
