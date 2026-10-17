"""研报存储 — 元数据 JSON(JsonReportStore 模式) + HTML 文件, 上限 50 份。

落盘: data/user_data/stock_reports.json (元数据, 原子写)
      data/user_data/stock_reports/stock-report-{id}.html (六项简报, 默认视图)
      data/user_data/stock_reports/stock-report-{id}-full.html (完整版, 可选)
超限裁剪时同步删除对应 HTML 文件。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

MAX_REPORTS = 50
_LOCK = threading.Lock()
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _base_dir() -> Path:
    from app.config import settings

    return settings.data_dir / "user_data"


def _meta_path() -> Path:
    return _base_dir() / "stock_reports.json"


def _report_file_path(report_id: str, suffix: str = "") -> Path:
    rid = str(report_id or "").strip()
    if not _SAFE_ID_RE.match(rid):
        # fail-closed: id 只允许字母数字/下划线/连横线, 防路径穿越;
        # 服务端生成的 id(sr<毫秒戳>/draft-<hex>)天然满足。
        raise ValueError(f"非法报告 id: {report_id!r}")
    return _base_dir() / "stock_reports" / f"stock-report-{rid}{suffix}.html"


def html_path(report_id: str) -> Path:
    """六项简报(默认视图)文件路径。"""
    return _report_file_path(report_id)


def full_html_path(report_id: str) -> Path:
    """完整版报告文件路径。"""
    return _report_file_path(report_id, "-full")


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
        records = [record, *_load()]
        for stale in records[MAX_REPORTS:]:
            stale_id = str(stale.get("id") or "")
            try:
                html_path(stale_id).unlink(missing_ok=True)
                full_html_path(stale_id).unlink(missing_ok=True)
            except OSError:
                logger.warning("[report-store] 旧报告删除失败: %s", stale_id)
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
