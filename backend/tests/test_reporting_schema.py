"""content_schema 契约: 15 块齐全、缺失校验、指标计算自洽。"""
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
