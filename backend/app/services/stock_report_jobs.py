"""研报任务编排 — 进程内任务表 + 单飞防重 + 后台 asyncio 执行。

状态: running → done / error / degraded(分片降级或填充率不足仍产出报告)。
重启后任务表清空, 前端轮询 404 时按「任务已丢失, 请重新生成」处理。
"""
from __future__ import annotations

import asyncio
import time
import uuid

JOB_KINDS = ("stock_report",)
_JOBS: dict[str, dict] = {}
# 后台任务引用集: 防止事件循环只持有弱引用导致任务中途被回收;
# 任务结束即从集合移除, 不长期占用内存。
_BACKGROUND: set = set()


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
    """把 runner(job) 挂到事件循环(测试替换为同步桩)。

    必须在运行中的事件循环内调用(API 端点与对话框流均满足该约束)。
    """
    task = asyncio.create_task(runner(job))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
