"""个股深度研报 API。

路由前缀: /api/stock-reports
  POST /generate                 触发生成(同 symbol 防重)
  GET  /{job_id}                 任务状态(阶段/进度/错误/报告 id)
  GET  /{job_id}/html            报告正文(text/html)
  GET  ?symbol=                  历史报告列表
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.services import stock_report_jobs, stock_report_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stock-reports", tags=["stock-reports"])


class GenerateRequest(BaseModel):
    """研报生成请求。"""

    symbol: str


@router.post("/generate")
async def generate(req: GenerateRequest):
    """触发生成; 同 symbol 已有 running 任务时返回已有任务(防重)。"""
    symbol = (req.symbol or "").strip().upper()
    if not symbol:
        raise HTTPException(400, "symbol 不能为空")
    running = stock_report_jobs.find_running(symbol)
    if running:
        return {"job": running, "deduped": True}
    job = stock_report_jobs.start(symbol, name="")

    def _runner(j):
        # pipeline 模块在调用时才导入(Task 13 提供), 本模块不做导入期依赖
        from app.services import stock_report_pipeline

        return stock_report_pipeline.run_report_job(j)

    stock_report_jobs._spawn(job, _runner)
    return {"job": job, "deduped": False}


@router.get("/{job_id}")
def get_status(job_id: str):
    """任务状态; 重启后任务表清空, 404 时前端按丢失处理。"""
    job = stock_report_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或已随进程重启丢失, 请重新生成")
    return {"job": job}


@router.get("/{job_id}/html", response_class=HTMLResponse)
def get_html(job_id: str):
    """报告正文; 元数据与文件必须同时存在, 否则 404/410。"""
    record = stock_report_store.get_report(job_id)
    if not record:
        raise HTTPException(404, "报告不存在")
    path = stock_report_store.html_path(job_id)
    if not path.exists():
        raise HTTPException(410, "报告文件已不存在(可能已被清理)")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@router.get("")
def list_reports(symbol: str = Query("", description="按标的过滤")):
    """历史报告列表(后端裁剪到上限, 按 created_at 降序)。"""
    return {"reports": stock_report_store.list_reports(symbol.strip())}
