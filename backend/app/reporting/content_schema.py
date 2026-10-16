"""content.json 结构定义与校验（零第三方依赖）。"""
from typing import Any

SCHEMA: dict[str, dict[str, Any]] = {
    "meta": {"name": str, "code": str, "analysis_date": str},
    "hero": {"price": str, "change_pct": str, "change_dir": str, "quality_score": (int, float),
             "target_price": str, "rr": str, "risk_level": str, "tags": list},
    "conclusion": {"big_verdict": str, "verdict_detail": str, "tags": list,
                   "trade_params": {"buy_range": str, "stop_loss": str, "target": str,
                                    "position": str, "rr": str}},
    "profile": {"paragraph": str, "tags": list, "info": list, "footnote": str},
    "mission": {"target": str, "cycle": str, "style": str, "thesis": str},
    "macro": {"cycle_position": str, "policy_scan": str, "core_contradiction": str},
    "chain": {"nodes": list, "trend_three": str, "profit_distribution": str, "judgment": str},
    "quality": {"positives": list, "negatives": list, "scores": list, "badge": str},
    "elasticity": {"tree_root": str, "branches": list, "sensitivity": str, "scenarios": list},
    "risk": {"matrix": list, "stop_signals": list},
    "valuation": {"methods": str, "targets": list, "rr": str, "fund_tech": str},
    "compare": {"rows": list, "engines": list},
    "tracking": {"catalysts": list, "checklist": list, "scores": list, "summary": str},
    "technical": {"verdict": str, "plan": list},
    "news": {"items": list},
}

MIN_LEN: dict[tuple[str, str], int] = {
    # 完整版报告沿用原有下限；六项简报视图只在展示层截断数组，不改数据契约。
    ("quality", "scores"): 6, ("quality", "positives"): 3, ("quality", "negatives"): 3,
    ("risk", "stop_signals"): 5, ("risk", "matrix"): 3,
    ("news", "items"): 5, ("chain", "nodes"): 3,
    ("elasticity", "branches"): 3, ("elasticity", "scenarios"): 3,
    ("compare", "rows"): 2, ("compare", "engines"): 3, ("valuation", "targets"): 3,
    ("tracking", "scores"): 5, ("tracking", "catalysts"): 1, ("tracking", "checklist"): 3,
    ("technical", "plan"): 3, ("hero", "tags"): 1, ("conclusion", "tags"): 1,
}

ITEM_KEYS: dict[tuple[str, str], tuple[str, ...]] = {
    ("profile", "info"): ("k", "v"),
    ("chain", "nodes"): ("stage", "name", "sub"),
    ("quality", "scores"): ("dim", "score", "reason"),
    ("elasticity", "branches"): ("name", "rev_pct", "gm_pct", "tier", "factor"),
    ("elasticity", "scenarios"): ("name", "price", "prob"),
    ("risk", "matrix"): ("risk", "impact", "prob", "mitigation"),
    ("valuation", "targets"): ("scenario", "price"),
    ("compare", "rows"): ("company", "metric", "value"),
    ("tracking", "catalysts"): ("event", "date", "status"),
    ("tracking", "scores"): ("dim", "score"),
    ("technical", "plan"): ("action", "trigger", "position"),
    ("news", "items"): ("text", "sentiment"),
}

SENTIMENTS = {"利好", "利空", "中性"}
SCORE_KEYS = {("quality", "scores"), ("tracking", "scores")}


def block_keys() -> list[str]:
    return list(SCHEMA)


def _check_block(name: str, spec: dict, blk: Any, errors: list[str]) -> None:
    # 注意：blk 即该区块的值（顶层由 validate_content 递入 obj[name]，嵌套为 dict 递归）。
    if not isinstance(blk, dict):
        errors.append(f"[{name}] 应为对象，实际 {type(blk).__name__}")
        return
    for key, typ in spec.items():
        if key not in blk:
            errors.append(f"[{name}.{key}] 字段缺失")
            continue
        if isinstance(typ, dict):
            if isinstance(blk[key], dict):
                _check_block(f"{name}.{key}", typ, blk[key], errors)
            else:
                errors.append(f"[{name}.{key}] 应为对象")
            continue
        if not isinstance(blk[key], typ) or (isinstance(blk[key], bool) and typ is not bool):
            errors.append(f"[{name}.{key}] 类型应为 {getattr(typ, '__name__', typ)}，"
                          f"实际 {type(blk[key]).__name__}")
        if typ is list and isinstance(blk[key], list):
            need = MIN_LEN.get((name, key))
            if need and len(blk[key]) < need:
                errors.append(f"[{name}.{key}] 至少需要 {need} 条，实际 {len(blk[key])} 条")
            keys = ITEM_KEYS.get((name, key))
            for i, item in enumerate(blk[key]):
                if keys:
                    if not isinstance(item, dict):
                        errors.append(f"[{name}.{key}][{i}] 应为对象")
                        continue
                    for k in keys:
                        if k not in item:
                            errors.append(f"[{name}.{key}][{i}].{k} 缺失")
                if ((name, key) == ("news", "items") and isinstance(item, dict)
                        and item.get("sentiment") not in SENTIMENTS):
                    errors.append(f"[{name}.{key}][{i}].sentiment 必须是 利好/利空/中性")
                if (name, key) in SCORE_KEYS and isinstance(item, dict):
                    sc = item.get("score")
                    if not isinstance(sc, (int, float)) or isinstance(sc, bool) or not 0 <= sc <= 100:
                        errors.append(f"[{name}.{key}][{i}].score 超出 0-100 范围")


def validate_content(obj: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["content 根节点应为对象"]
    for name, spec in SCHEMA.items():
        if name not in obj:
            errors.append(f"[{name}] 区块缺失")
            continue
        _check_block(name, spec, obj[name], errors)
    return errors
