"""个股深度研报引擎 — NDJSON 适配(对话框) + 后台任务(采集→分片→渲染→终验→存储)。

事件协议与 DSA/legacy 引擎一致: meta / delta / error / done;
done 额外携带 report_id / report_url, 前端据此渲染「打开报告」。
focus 参数为与其它引擎统一的入口签名, 研报管线本身不消费(摘要来自数据块)。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from app.services import report_shards, stock_report_data, stock_report_jobs, stock_report_store
from app.services.stock_report_data import ReportDataUnavailableError

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


async def run_report_job(job: dict) -> None:
    """后台任务主体; 任何异常都必须落到 job.status=error(前端不能永久转圈)。"""
    symbol, name = job["symbol"], job["name"]
    try:
        stock_report_jobs.update(job["id"], phase="collect", progress=5)
        blocks, missing = await asyncio.to_thread(stock_report_data.collect_blocks, symbol)
        code = symbol.split(".")[0]
        if not name:
            name = report_shards.resolve_company_name(blocks, code)
        stock_report_jobs.update(job["id"], phase="analyze", progress=30, name=name)

        content, failed = await report_shards.analyze_shards(code, name, blocks)
        rate = report_shards.fill_rate(content)
        stock_report_jobs.update(job["id"], phase="render", progress=80)

        from app.reporting import render_report, validate_html

        html = await asyncio.to_thread(render_report.render, content, blocks)
        stock_report_jobs.update(job["id"], phase="validate", progress=90)
        draft = stock_report_store.html_path(f"draft-{job['id']}")
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(html, encoding="utf-8")
        errors = await asyncio.to_thread(validate_html.check_html, str(draft))
        if errors:
            draft.unlink(missing_ok=True)
            stock_report_jobs.update(
                job["id"], status="error", phase="validate",
                error=f"终验 {len(errors)} 项飘红: {errors[0]}")
            return

        status = "degraded" if (failed or rate < 0.5 or missing) else "done"
        record = stock_report_store.save_report(
            {"symbol": symbol, "name": name, "status": status, "fill_rate": rate,
             "failed_shards": failed, "missing_blocks": missing}, html)
        stock_report_jobs.update(
            job["id"], status="done", phase="done", progress=100,
            report_id=record["id"],
            report_url=f"/api/stock-reports/{record['id']}/html",
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
        job = stock_report_jobs.start(symbol, "")
        job["_spawned"] = True
        stock_report_jobs._spawn(job, lambda j: run_report_job(j))
    yield _event({"type": "meta", "symbol": symbol,
                  "summary": "深度研报生成中(通常 3-8 分钟)", "job_id": job["id"]})
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
                  "degraded": bool(job.get("degraded"))})
