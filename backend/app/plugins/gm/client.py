"""GM 桥接客户端 — 独立 venv 管理 + 单发子进程调用。

为什么不用主 venv 直接 import gm:
  gm 3.0.190 把 numpy 钉到 1.26.4 / pandas 钉到 1.5.3,
  装进后端主 venv 会把项目锁定的 numpy 2.x / pandas 3.x 降级,
  属于高风险依赖污染。独立 venv (data/gm-env) 做进程级隔离,
  后端主流程零依赖变更; 单次 spawn 开销由桥接内批量循环摊销。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

API_KEY_ENV = "GM_API_KEY"
SECRETS_FIELD = "gm_api_key"

_BRIDGE = Path(__file__).resolve().parent / "bridge.py"
_REQUIREMENTS = Path(__file__).resolve().parent / "requirements.txt"


class GmError(RuntimeError):
    pass


def get_api_key() -> str:
    from app import secrets_store

    return secrets_store.get_env_backed_secret(SECRETS_FIELD, API_KEY_ENV)


def _venv_dir() -> Path:
    from app.config import settings

    return settings.data_dir / "gm-env"


def _venv_python() -> Path:
    d = _venv_dir()
    if os.name == "nt":
        return d / "Scripts" / "python.exe"
    return d / "bin" / "python"


def venv_ready() -> bool:
    """gm venv 是否就绪 (python 存在且能 import gm)。只做轻量检查, 不安装。"""
    py = _venv_python()
    if not py.exists():
        return False
    try:
        proc = subprocess.run(
            [str(py), "-c", "import gm.api"],
            capture_output=True, timeout=60,
        )
    except Exception:  # noqa: BLE001
        return False
    return proc.returncode == 0


def ensure_venv(timeout_s: float = 600.0) -> None:
    """确保 gm venv 就绪, 缺失则用 uv 创建并安装 requirements。失败抛 GmError。"""
    if venv_ready():
        return
    uv = shutil.which("uv")
    if not uv:
        raise GmError("未找到 uv, 无法自动创建 GM 数据环境 (请先安装 uv 后重试)")
    d = _venv_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    logger.info("GM 数据环境缺失, 正在创建 (%s), 约需 1 分钟…", d)
    try:
        r = subprocess.run([uv, "venv", str(d)], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise GmError(f"uv venv 失败: {(r.stderr or r.stdout)[-300:]}")
        r = subprocess.run(
            [uv, "pip", "install", "--python", str(_venv_python()), "-r", str(_REQUIREMENTS)],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if r.returncode != 0:
            raise GmError(f"gm SDK 安装失败: {(r.stderr or r.stdout)[-300:]}")
    except subprocess.TimeoutExpired as e:
        raise GmError(f"GM 数据环境安装超时: {e}") from e
    if not venv_ready():
        raise GmError("GM 数据环境创建后自检失败 (import gm.api 不通过)")
    logger.info("GM 数据环境就绪: %s", d)


def run_op(op: str, params: dict | None = None, timeout_s: float = 300.0) -> list[dict]:
    """执行一次桥接调用, 返回行列表。失败抛 GmError (上层按空数据/回退处理)。"""
    ensure_venv()
    key = get_api_key()
    if not key:
        raise GmError("GM Token 未配置 (设置页填写或配置环境变量 GM_API_KEY)")
    req = {"token": key, "op": op, "params": params or {}}
    fd, req_path = tempfile.mkstemp(prefix="gm_req_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(req, f)
        try:
            proc = subprocess.run(
                [str(_venv_python()), str(_BRIDGE), req_path],
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            raise GmError(f"GM 调用超时 ({timeout_s:.0f}s, op={op})") from e
        payload = None
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if payload is None:
            raise GmError(f"GM 桥接无输出 (exit={proc.returncode}, stderr={(proc.stderr or '').strip()[-300:]})")
        if not payload.get("ok"):
            raise GmError(f"GM 调用失败 (op={op}): {payload.get('error', '未知原因')}")
        return payload.get("rows") or []
    finally:
        with contextlib.suppress(OSError):
            os.unlink(req_path)


def probe_token(api_key: str, timeout_s: float = 60.0) -> tuple[bool, str]:
    """用候选 Key 实探一次 current (先探后存, 不落盘)。需 venv 就绪, 否则提示安装。"""
    if not (api_key or "").strip():
        return False, "Key 为空"
    if not venv_ready():
        try:
            ensure_venv()
        except GmError as e:
            return False, f"{e} (可稍后在设置页重试)"
    req = {"token": api_key.strip(), "op": "current", "params": {"symbols": ["SHSE.600519"]}}
    fd, req_path = tempfile.mkstemp(prefix="gm_probe_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(req, f)
        try:
            proc = subprocess.run(
                [str(_venv_python()), str(_BRIDGE), req_path],
                capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return False, f"探测超时 ({timeout_s:.0f}s), 请检查网络后重试"
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("ok") and payload.get("rows"):
                    return True, "ok"
                return False, f"Key 无效或网络失败: {str(payload.get('error', '空数据'))[:200]}"
        return False, f"探测失败: 桥接无输出 (exit={proc.returncode})"
    finally:
        with contextlib.suppress(OSError):
            os.unlink(req_path)
