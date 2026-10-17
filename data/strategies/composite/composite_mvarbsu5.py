"""叠加策略 composite_mvarbsu5（自动生成, 请勿手改业务逻辑）。"""
META = {
    "id": 'composite_mvarbsu5',
    "name": '高股息筛选+通达信红箭头',
    "description": '',
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "merge_mode", "label": "合并模式", "type": "select",
          "options": ["union", "intersect"], "default": 'intersect'},
        {"id": "min_confirm", "label": "交集最少确认数", "type": "int",
          "default": 0, "min": 0},
    ],
    "scoring": {},
    "order_by": "score",
    "descending": True,
    "limit": 100,
    "children": [
        {"strategy_id": "custom_high_dividend", "weight": 0.5},
        {"strategy_id": "custom_tdx_red_arrow", "weight": 0.5}
    ],
}
EXECUTION_BACKEND = "composite"
