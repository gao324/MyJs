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
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"输入不可读: {path}({type(exc).__name__})") from exc
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, dict):
        raise ValueError(f"输入缺少 blocks 对象: {path}")
    return {k: v for k, v in blocks.items() if not str(k).startswith("_")}


def compare_blocks(stock_ai: dict, ours: dict) -> list[str]:
    """逐块比对 stock_ai 与本管线的数据块, 返回差异行(空 = 一致)。

    kline_minute 按设计不采集, 跳过比对; 其余块逐值比较(不比较原始 JSON 字节)。
    """
    diffs: list[str] = []
    for name in sorted(set(stock_ai) | set(ours)):
        if name == "kline_minute":
            continue
        a, b = stock_ai.get(name), ours.get(name)
        if a == b:
            continue
        if isinstance(a, list) and isinstance(b, list):
            diffs.append(f"{name}: 行数 {len(a)} vs {len(b)}")
            continue
        diffs.append(f"{name}: 内容差异")
    return diffs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="对比 stock_ai 与本管线数据块")
    ap.add_argument("--stock-ai", required=True)
    ap.add_argument("--ours", required=True)
    args = ap.parse_args(argv)

    try:
        left = _load_blocks(Path(args.stock_ai))
        right = _load_blocks(Path(args.ours))
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2
    diffs = compare_blocks(left, right)
    if not diffs:
        print("✅ 数据块一致")
        return 0
    print("❌ 数据块差异:")
    for line in diffs:
        print(f"  - {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
