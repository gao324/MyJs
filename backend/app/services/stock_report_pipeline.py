"""个股深度研报引擎 — NDJSON 适配(对话框) + 后台任务(采集→分片→渲染→终验→存储)。

事件协议与 DSA/legacy 引擎一致: meta / delta / error / done;
done 额外携带 report_id / report_url / full_report_url: 简报为默认视图,
完整版仅在结构性终验通过时产出(否则报告标记降级并隐藏入口)。
focus 参数为与其它引擎统一的入口签名, 研报管线本身不消费(摘要来自数据块)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from app.services import report_shards, stock_report_data, stock_report_jobs, stock_report_store
from app.services.fs_utils import atomic_write_text
from app.services.heavy_job_limiter import heavy_job_limiter
from app.services.stock_report_data import ReportDataUnavailableError

logger = logging.getLogger(__name__)

PHASE_LABELS = {
    "queued": "等待系统资源(数据同步/修复或回测等后台任务运行中)",
    "collect": "Phase 1/4 数据采集中",
    "analyze": "Phase 2/4 七分片分析中",
    "render": "Phase 3/4 模板渲染中",
    "validate": "Phase 4/4 终验中",
}
POLL_SECONDS = 2.0  # 对话框进度轮询间隔(测试 monkeypatch 缩短)
# 重任务额度等待上限(秒): 数据同步/修复等独占任务可能持续数分钟, 期间研报排队
# 而不是直接失败; 超过上限仍拿不到额度才报错(测试 monkeypatch 缩短)。
HEAVY_ACQUIRE_TIMEOUT = 600.0



def _collect_rotation_context(repo) -> dict:
    """复用本地行业/概念 RPS 数据；不可用时交由报告明确标注，不影响主报告。"""
    if repo is None:
        return {"available": False}

    def leaders(matrix: dict) -> dict:
        dates = matrix.get("dates") if isinstance(matrix, dict) else None
        columns = matrix.get("columns") if isinstance(matrix, dict) else None
        if not isinstance(dates, list) or not isinstance(columns, dict):
            return {"rows": [], "window": 0, "as_of": ""}
        recent = dates[:5]
        totals: dict[str, list[float]] = {}
        covered_days: set[str] = set()
        for day in recent:
            rows = columns.get(day, [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                name = str(row[0] or "").strip()
                try:
                    pct = float(row[1])
                except (TypeError, ValueError):
                    continue
                if name and math.isfinite(pct):
                    totals.setdefault(name, []).append(pct)
                    covered_days.add(str(day))
        ranked = sorted(
            ((name, sum(values) / len(values)) for name, values in totals.items()),
            key=lambda item: item[1], reverse=True,
        )[:5]
        return {
            "rows": [{"name": name, "change_pct": round(avg * 100, 2)}
                     for name, avg in ranked],
            "window": len(covered_days),
            "as_of": str(dates[0]) if dates else "",
        }

    try:
        from app.services import rps_rotation

        industry = rps_rotation.build_rps_rotation(repo, days=12, kind="industry", level=2)
        concepts = rps_rotation.build_rps_rotation(repo, days=12, kind="concept")
        industry_data = leaders(industry)
        concept_data = leaders(concepts)
        dates = [industry_data["as_of"], concept_data["as_of"]]
        return {
            "available": bool(industry_data["rows"] or concept_data["rows"]),
            "as_of": max((day for day in dates if day), default=""),
            "industry_window": industry_data["window"],
            "concept_window": concept_data["window"],
            "industry": industry_data["rows"],
            "concept": concept_data["rows"],
        }
    except Exception as exc:  # 轮动是可选素材，缺失不能阻断个股报告  # noqa: BLE001
        logger.info("[report-job] 行业轮动素材不可用: %s", type(exc).__name__)
        return {"available": False}


def _event(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def run_report_job(job: dict, repo=None) -> None:
    """后台任务主体; 任何异常都必须落到 job.status=error(前端不能永久转圈)。

    并发约束走 heavy_job_limiter 共享预算: 数据管道/修复等独占任务运行期间
    研报任务排队等待(前端可见 queued 阶段), 超过 HEAVY_ACQUIRE_TIMEOUT 仍未
    拿到额度才报错, 不再出现"管道在跑时研报必然秒失败"。
    """
    stock_report_jobs.update(job["id"], phase="queued", progress=0)
    acquired = await asyncio.to_thread(
        heavy_job_limiter.acquire, "normal", timeout=HEAVY_ACQUIRE_TIMEOUT)
    if not acquired:
        minutes = int(HEAVY_ACQUIRE_TIMEOUT // 60)
        stock_report_jobs.update(
            job["id"], status="error", phase="queued",
            error=f"系统繁忙: 数据同步/修复或回测等后台任务占用资源, "
                  f"等待 {minutes} 分钟仍未获得执行额度, 请稍后重试")
        return
    try:
        await _run_report_job(job, repo)
    finally:
        heavy_job_limiter.release("normal")


def _validate_draft(html: str, job_id: str, checker: Callable[[str], list[str]],
                    *, full: bool) -> list[str]:
    """草稿写盘跑结构性终验; 无论结果如何都清理草稿文件。"""
    path = (stock_report_store.full_html_path(f"draft-{job_id}") if full
            else stock_report_store.html_path(f"draft-{job_id}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    try:
        return checker(str(path))
    finally:
        path.unlink(missing_ok=True)


async def _run_report_job(job: dict, repo=None) -> None:
    symbol, name = job["symbol"], job["name"]
    try:
        stock_report_jobs.update(job["id"], phase="collect", progress=5)
        blocks, missing = await asyncio.to_thread(stock_report_data.collect_blocks, symbol)
        blocks["_industry_rotation"] = await asyncio.to_thread(_collect_rotation_context, repo)
        code = symbol.split(".")[0]
        if not name:
            name = report_shards.resolve_company_name(blocks, code)
        stock_report_jobs.update(job["id"], phase="analyze", progress=30, name=name)

        content, failed = await report_shards.analyze_shards(code, name, blocks)
        rate = report_shards.fill_rate(content)
        stock_report_jobs.update(job["id"], phase="render", progress=80)

        from app.reporting import render_report, validate_html, validate_html_full

        compact_html = await asyncio.to_thread(render_report.render, content, blocks)
        try:
            full_html = await asyncio.to_thread(render_report.render_full, content, blocks)
        except Exception as exc:  # 完整版是可选视图: 渲染失败只降级
            logger.exception("[report-job] 完整版渲染失败, 仅发布简报: %s", exc)
            full_html = None
        stock_report_jobs.update(job["id"], phase="validate", progress=90)

        errors = await asyncio.to_thread(
            _validate_draft, compact_html, job["id"], validate_html.check_html, full=False)
        if errors:
            stock_report_jobs.update(
                job["id"], status="error", phase="validate",
                error=f"终验 {len(errors)} 项飘红: {errors[0]}")
            return
        if full_html is not None:
            full_errors = await asyncio.to_thread(
                _validate_draft, full_html, job["id"],
                validate_html_full.check_html, full=True)
            if full_errors:
                logger.warning("[report-job] 完整版终验失败, 仅发布简报: %s", full_errors[0])
                full_html = None

        status = "degraded" if (failed or rate < 0.5 or missing
                                or full_html is None) else "done"
        record = stock_report_store.save_report(
            {"symbol": symbol, "name": name, "status": status, "fill_rate": rate,
             "failed_shards": failed, "missing_blocks": missing,
             "full_available": full_html is not None}, compact_html)
        report_id = record["id"]
        if full_html is not None:
            full_out = full_html.replace(
                'data-brief-report="pending"',
                f'href="/api/stock-reports/{report_id}/html" data-brief-report="ready"')
            atomic_write_text(stock_report_store.full_html_path(report_id), full_out)
            compact_out = compact_html.replace(
                'data-full-report="pending"',
                f'href="/api/stock-reports/{report_id}/full-html" data-full-report="ready"')
            full_url = f"/api/stock-reports/{report_id}/full-html"
        else:
            compact_out = compact_html.replace(
                'data-full-report="pending"', 'hidden data-full-report="unavailable"')
            full_url = ""
        atomic_write_text(stock_report_store.html_path(report_id), compact_out)
        stock_report_jobs.update(
            job["id"], status="done", phase="done", progress=100,
            report_id=report_id,
            report_url=f"/api/stock-reports/{report_id}/html",
            full_report_url=full_url,
            degraded=status == "degraded")
    except ReportDataUnavailableError as exc:
        stock_report_jobs.update(job["id"], status="error", phase="collect", error=str(exc))
    except Exception as exc:  # 任务级兜底: 任务状态必须落地, 不抛到事件循环
        logger.exception("[report-job] 任务失败: %s", job["id"])
        stock_report_jobs.update(job["id"], status="error", error=f"{type(exc).__name__}: {exc}")


async def analyze_stock_stream(repo, data_dir: Path, symbol: str,
                               focus: str = "") -> AsyncIterator[str]:
    """对话框入口: 立即返回 meta, 轮询 job 进度产生 delta, 终态产生 done/error。"""
    symbol = (symbol or "").strip().upper()
    job = stock_report_jobs.find_running(symbol)
    if job is None:
        # find → start → spawn 之间无 await, 单线程事件循环内天然原子, 不会重复派发
        job = stock_report_jobs.start(symbol, "")
        stock_report_jobs._spawn(job, lambda j: run_report_job(j, repo))
    yield _event({"type": "meta", "symbol": symbol,
                  "summary": "六项简报 + 完整版生成中(通常 3-8 分钟)",
                  "job_id": job["id"]})
    seen = ""
    while True:
        await asyncio.sleep(POLL_SECONDS)
        phase = job.get("phase", "")
        if phase != seen and phase in PHASE_LABELS:
            seen = phase
            yield _event({"type": "delta",
                          "content": f"\n- {PHASE_LABELS[phase]} "
                                     f"({job.get('progress', 0)}%)\n"})
        if job.get("status") in ("done", "error"):
            break
    if job.get("status") == "error":
        yield _event({"type": "error", "message": job.get("error") or "研报生成失败"})
        return
    yield _event({"type": "done", "report_id": job.get("report_id", ""),
                  "report_url": job.get("report_url", ""),
                  "full_report_url": job.get("full_report_url", ""),
                  "degraded": bool(job.get("degraded"))})
