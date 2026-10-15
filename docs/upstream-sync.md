# 自用分支与上游跟随维护文档

本仓库是 `shy3130/tick-stock-panel` 的自用二开版本：日常在 `main` 上直接用，
上游有更新时按下面的固定流程合并，既不错过上游修复，也不丢自己的定制。

## 分支设计

| 分支 | 指向 | 用途 | 能否直接改 |
| --- | --- | --- | --- |
| `main` | 自用代码（含全部定制） | 日常使用、开发、推送 | ✅ 唯一工作分支 |
| `vendor/upstream` | 纯净上游代码（无任何定制） | 合并上游时的“共同基线”， diff 参照物 | ❌ 永远不要在上面提交 |

远端：

| 远端名 | 地址 | 用途 |
| --- | --- | --- |
| `origin` | `https://github.com/shy3130/tick-stock-panel.git` | 只 fetch 上游更新，不 push |
| `myjs` | `https://github.com/gao324/MyJs.git` | 自己的仓库，`main` 推到这里 |

当前定制基线（`main` 相对上游多出的内容）：GM 数据源插件及默认路由、
登录校验关闭、火山方舟 AI 配置（Key 只存本地 `.env`，不进仓库）、
交易日历 gm 优先。详细见 `git log main --not vendor/upstream`。

## 跟随上游更新（标准流程）

```bash
# 1. 取上游最新，但不动工作区
git fetch origin

# 2. 只读预检：看上游改了哪些文件、是否与定制冲突
python3 scripts/upgrade_check.py origin/main

# 3. 把纯净基线推到上游最新
git checkout vendor/upstream
git merge --ff-only origin/main
git checkout main

# 4. 把上游合并进自用分支（保留双方改动，冲突逐个解）
git merge vendor/upstream

# 5. 按 CONTRIBUTING 验证矩阵回归（至少）：
cd backend && uv run pytest tests/test_gm_provider.py tests/test_trading_day.py \
  tests/test_capability_matrix.py tests/test_data_integrity.py -q
cd ../frontend && pnpm build
git diff --check

# 6. 推到自己的仓库
git push myjs main
git push myjs vendor/upstream
```

## 冲突处理原则

- 上游改了你也改过的文件（高频区：`backend/app/main.py`、
  `frontend/src/router.tsx`、`frontend/src/lib/api.ts`）：以“三方合并”视角处理——
  `vendor/upstream` 是“改之前”，`origin/main` 是“上游改之后”，`main` 是“你的改之后”，
  三者对比，只把上游的新东西接进来，自己的定制保留。
- 上游新增的数据源/能力：默认不影响你（你的偏好已显式指向 gm）。
- 上游若重构了你依赖的 provider 接口（如 `get_realtime` 签名），
  同步修改 `backend/app/plugins/gm/provider.py` 并补测试。
- 合并后必跑一遍后端启动 + 设置页能力矩阵，确认 gm 仍为 effective 且 usable。

## 回滚

某次跟随更新后出问题：`git log` 找到合并前的 `main`  commit，
`git reset --hard <commit>` 回到合并前（先确认无未提交改动），
再 `git push --force myjs main`。`vendor/upstream` 不动，下次还可重合。
