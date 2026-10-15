"""研报存储 — 元数据 JSON(JsonReportStore 模式) + HTML 文件, 上限 50 份。

落盘: data/user_data/stock_reports.json (元数据, 原子写)
      data/user_data/stock_reports/stock-report-{id}.html (正文)
超限裁剪时同步删除对应 HTML 文件。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from app.services.fs_utils import atomic_write_text

logger = logging.getLogger(__name__)

MAX_REPORTS = 50
_LOCK = threading.Lock()


def _base_dir() -> Path:
    from app.config import settings

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
        records = [record, *_load()]
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
