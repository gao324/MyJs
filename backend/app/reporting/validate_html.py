"""终验自检脚本：17 条（任务书 16 条 + 第17条 正文滚动容器），产出物上牌前的闸门。

设计口径（重要，勿照搬文档里的 grep 行数）：
- 全部用「子串出现次数 / 正则」实现，**不**套 `HTML手写参考.md` 里 `grep -c`
  的**按行**阈值——那是给「分批部分文件」写的，套到整篇会误判。
- 阈值一律以金标准范例 `examples/个股研究-中国长城.html`（1156 行，人工验证产物）
  实测校准；同类名同时出现在 CSS 与正文时（如 `hero-meta-item`），只数完整标签
  形态 `'<div class="hero-meta-item">'`（该串范例 5 个；裸子串 `hero-meta-item` 是
  10 个，含 5 条 CSS 规则）；不同写法则分开数（`<span class="score-fill"` 0 个 /
  `<div class="score-fill" style="width:` 18 个）。
- 第14条取「文档并集」口径：`催化剂兑现追踪|已兑现|待兑现` 任一出现即算跟踪日志在
  位。原因：`content_schema` 的 `tracking.catalysts[].status` 是自由文本（夹具给的
  是"未验证"），若硬数 `待兑现/已兑现` 两个枚举词，会把合法报告误判成红项。

接口：
- `check_html(path) -> list[str]`：红项描述列表，`[]` 表示全绿；每条格式
  `第N条 <检查名>：<细节>`。
- CLI：`python validate_html.py <file>`，全绿退 0 / 有红项退 1 / 用法或文件错误退 2，
  并逐条打印状态。
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable

# ---------------------------------------------------------------- 校准常量
NAV_ANCHOR_COUNT = 14          # 金标准范例 nav 锚点（含技术面）
SCRIPT_CLOSE_MIN = 4           # 3 个 CDN + 1 段主 JS
HERO_META_COUNT = 5            # 正文 hero-meta-item（完整 div 形态）
TAIL_WINDOW = 2000             # 主 JS 闭合必须落在文末这么长的窗口内
SENTIMENT_MIN = 5              # 新闻情绪标签（利好/利空/中性）最少条数
FOOTER_NEEDS = (               # 第10条 Footer 结构（署名/联系方式已按要求移除，不再校验）
    ("免责声明", 1),
    ("分析基准日", 2),          # hero + footer 两处；nav 的日期由第15条校验
)
TRACKING_MARKERS = ("催化剂兑现追踪", "已兑现", "待兑现")  # 第14条（范例合计 10）
EVENT_LOG = "📋 事件记录"
OHLC_GOOD = "ohlc.push([d[1], d[4], d[3], d[2]])"
OHLC_BAD_RE = re.compile(r"ohlc\.push\(\[d\[1\], d\[2\]")
# 双反斜杠的 MathJax 公式（`\\times` 等，范例 0 处）
DBL_BACKSLASH_RE = re.compile(r"\\\\times|\\\\frac|\\\\alpha")
PLACEHOLDER_RE = re.compile(r"__[A-Z_]+__|\{\{[A-Z0-9_]+\}\}")
NUMBERED_SUBTITLE_RE = re.compile(r"<(h3|strong)[^>]*>[0-9]+[a-c]\.")
INLINE_WHITE_RE = re.compile(r'style="[^"]*color:\s*(#fff|white|rgba\(255)',
                                 re.IGNORECASE)
HREF_RE = re.compile(r'href="#([^"]+)"')
ID_RE = re.compile(r'id="([^"]+)"')


# ---------------------------------------------------------------- 逐条检查
# 每个检查返回 (错误明细列表, 通过时的计量说明)；说明用于 CLI 逐条打印。
def _chk_nav_anchor_count(html: str) -> tuple[list[str], str]:
    """第1条 导航锚点配对：nav 的 href="#..." 恰好 14 个。"""
    n = len(HREF_RE.findall(html))
    errs = [] if n == NAV_ANCHOR_COUNT else [
        f"nav 锚点 {n} 个，应为 {NAV_ANCHOR_COUNT}（含技术面）"]
    return errs, f"nav 锚点 {n} 个"


def _chk_anchor_pairs(html: str) -> tuple[list[str], str]:
    """第2条 锚点逐对验证：每个 href="#x" 都要有 id="x"。"""
    ids = set(ID_RE.findall(html))
    miss = [a for a in HREF_RE.findall(html) if a not in ids]
    errs = [f"无对应 id {miss}"] if miss else []
    return errs, f"id {len(ids)} 个，死链 {len(miss)} 个"


def _chk_html_close(html: str) -> tuple[list[str], str]:
    """第3条 </html> 唯一。"""
    n = html.count("</html>")
    return ([f"{n} 次，应为 1"] if n != 1 else []), f"{n} 次"


def _chk_script_close(html: str) -> tuple[list[str], str]:
    """第4条 </script> 齐全（≥ 3 CDN + 1 主 JS），防「全页 JS 炸裂」。"""
    n = html.count("</script>")
    return ([f"{n} < {SCRIPT_CLOSE_MIN}（3 CDN + 1 主JS）"]
            if n < SCRIPT_CLOSE_MIN else []), f"{n} 个"


def _chk_script_tail(html: str) -> tuple[list[str], str]:
    """第5条 主 JS 闭合在末尾（尾部窗口内必须出现 </script>）。"""
    ok = "</script>" in html[-TAIL_WINDOW:]
    return ([] if ok else [f"尾部 {TAIL_WINDOW} 字符内无 </script>"]), \
        ("在末尾" if ok else "不在末尾")


def _chk_score_fill_div(html: str) -> tuple[list[str], str]:
    """第6条 评分条用 <div>：出现 `<span class="score-fill"` 即红（色条不显示）。"""
    span = html.count('<span class="score-fill"')
    div = html.count('<div class="score-fill" style="width:')
    errs = [f'出现 <span class="score-fill" {span} 个（评分条必须用 div + width）']
    return (errs if span else []), f'<span 形态 {span} 个 / <div 形态 {div} 个'


def _chk_mathjax(html: str) -> tuple[list[str], str]:
    """第7条 MathJax 单反斜杠：出现双反斜杠公式即红（公式不渲染）。"""
    hits = DBL_BACKSLASH_RE.findall(html)
    return ([f"出现双反斜杠公式 {len(hits)} 处"] if hits else []), \
        f"双反斜杠公式 {len(hits)} 处"


def _chk_placeholders(html: str) -> tuple[list[str], str]:
    """第8条 占位符清零：`__X__` / `{{X}}` 残留即红。"""
    ph = PLACEHOLDER_RE.findall(html)
    errs = [f"残留 {sorted(set(ph))[:5]}（共 {len(ph)} 处）"] if ph else []
    return errs, f"残留 {len(ph)} 处"


def _chk_numbered_subtitle(html: str) -> tuple[list[str], str]:
    """第9条 子标题无编号前缀：`<h3>1a.` / `<strong>2b.` 之类格式污染即红。"""
    hits = NUMBERED_SUBTITLE_RE.findall(html)
    return ([f"命中 {len(hits)} 处编号前缀"] if hits else []), f"编号前缀 {len(hits)} 处"


def _chk_footer(html: str) -> tuple[list[str], str]:
    """第10条 Footer 结构完整：免责声明 ≥1，分析基准日 ≥2（hero+footer）。"""
    errs = [f"{kw} 出现 {html.count(kw)} 次（需 ≥{need}）"
            for kw, need in FOOTER_NEEDS if html.count(kw) < need]
    info = "、".join(f"{kw}×{html.count(kw)}" for kw, _ in FOOTER_NEEDS)
    return errs, info


def _chk_hero_meta(html: str) -> tuple[list[str], str]:
    """第10条 Hero meta 结构：正文完整 div 形态恰好 5 个（CSS 规则不计）。"""
    n = html.count('<div class="hero-meta-item">')
    errs = [] if n == HERO_META_COUNT else [
        f"正文 hero-meta-item {n} 个，应为 {HERO_META_COUNT}"]
    return errs, f"{n} 个"


def _chk_inline_white(html: str) -> tuple[list[str], str]:
    """第11条 内联硬编码白色清零（浅色模式下白字不可见）。"""
    hits = INLINE_WHITE_RE.findall(html)
    errs = [f"命中 {len(hits)} 处：{sorted(set(hits))}"] if hits else []
    return errs, f"命中 {len(hits)} 处"


def _chk_sentiment(html: str) -> tuple[list[str], str]:
    """第12条 新闻情绪标签齐全：利好/利空/中性 合计 ≥ 5。"""
    n = html.count("利好") + html.count("利空") + html.count("中性")
    errs = [f"利好/利空/中性 合计 {n} 处，需 ≥{SENTIMENT_MIN}"] if n < SENTIMENT_MIN else []
    return errs, f"情绪标签 {n} 处"


def _chk_kline_marks(html: str) -> tuple[list[str], str]:
    """第13条 K线技术标注：markLine / markPoint 各 ≥ 1。"""
    ml, mp = html.count("markLine"), html.count("markPoint")
    errs = []
    if ml < 1:
        errs.append(f"markLine {ml} 处（需 ≥1）")
    if mp < 1:
        errs.append(f"markPoint {mp} 处（需 ≥1）")
    return errs, f"markLine {ml} / markPoint {mp}"


def _chk_tracking(html: str) -> tuple[list[str], str]:
    """第14条 跟踪日志完整：兑现追踪标记（并集）≥1 且 `📋 事件记录` 在。"""
    n = sum(html.count(k) for k in TRACKING_MARKERS)
    errs = []
    if n < 1:
        errs.append("兑现追踪标记（" + "/".join(TRACKING_MARKERS) + "）0 处（需 ≥1）")
    if EVENT_LOG not in html:
        errs.append(f"缺少 {EVENT_LOG}")
    return errs, f"兑现标记 {n} 处 / 事件记录 {'有' if EVENT_LOG in html else '无'}"


def _chk_nav_date(html: str) -> tuple[list[str], str]:
    """第15条 分析日期在 nav：`nav-analysis-date` 至少 1 处（范例 2：CSS + nav）。"""
    n = html.count("nav-analysis-date")
    errs = ["nav-analysis-date 缺失"] if n < 1 else []
    return errs, f"{n} 处（CSS 规则 + nav 元素）"


def _chk_ohlc(html: str) -> tuple[list[str], str]:
    """第16条 OHLC 代码正确：错误的 `[d[1], d[2]` 形式 = 0 且正确写法 ≥ 1。"""
    bad = len(OHLC_BAD_RE.findall(html))
    good = html.count(OHLC_GOOD)
    errs = []
    if bad:
        errs.append(f"出现错误的 [d[1], d[2] 形式 {bad} 处（K线会全红无绿）")
    if good < 1:
        errs.append(f"缺少正确写法 {OHLC_GOOD}")
    return errs, f"正确写法 {good} 处 / 错误写法 {bad} 处"


def _chk_no_scroll_in_body(html: str) -> tuple[list[str], str]:
    """第17条 正文滚动容器清零：`overflow-y` / `max-height:300` 只能待在注入的
    `<style>` 里（`shared/template_base.css` 的 `.news-list`），`</style>` 之后必须为 0；
    同时 `<style>` 块应为 1 个，否则「样式块内」这个口径失去意义。"""
    style_n = len(re.findall(r"<style", html))
    i = html.rfind("</style>")
    body = html[i + len("</style>"):] if i >= 0 else html
    oy, mh = body.count("overflow-y"), body.count("max-height:300")
    errs = []
    if oy:
        errs.append(f"正文（</style> 之后）出现 overflow-y {oy} 处，"
                    "滚动容器应留在注入的 <style> 内")
    if mh:
        errs.append(f"正文（</style> 之后）出现 max-height:300 {mh} 处，"
                    "应留在注入的 <style> 内")
    if style_n != 1:
        errs.append(f"<style> 块 {style_n} 个，应为 1（CSS 注入异常时本条会失真）")
    return errs, f"<style> {style_n} 个 / 正文 overflow-y {oy}、max-height:300 {mh}"


# (编号, 检查名, 检查函数) —— 顺序即 1..17；第10条含 Footer 与 Hero meta 两个子项
CHECKS: tuple[tuple[str, str, Callable[[str], tuple[list[str], str]]], ...] = (
    ("第1条", "导航锚点配对", _chk_nav_anchor_count),
    ("第2条", "锚点逐对验证", _chk_anchor_pairs),
    ("第3条", "</html> 唯一", _chk_html_close),
    ("第4条", "</script> 齐全", _chk_script_close),
    ("第5条", "主JS闭合在末尾", _chk_script_tail),
    ("第6条", "评分条用 div", _chk_score_fill_div),
    ("第7条", "MathJax 单反斜杠", _chk_mathjax),
    ("第8条", "占位符清零", _chk_placeholders),
    ("第9条", "子标题无编号前缀", _chk_numbered_subtitle),
    ("第10条", "Footer 结构完整", _chk_footer),
    ("第10条", "Hero meta 结构", _chk_hero_meta),
    ("第11条", "内联硬编码白色清零", _chk_inline_white),
    ("第12条", "新闻情绪标签齐全", _chk_sentiment),
    ("第13条", "K线技术标注", _chk_kline_marks),
    ("第14条", "跟踪日志完整", _chk_tracking),
    ("第15条", "分析日期在nav", _chk_nav_date),
    ("第16条", "OHLC 代码", _chk_ohlc),
    ("第17条", "正文滚动容器清零", _chk_no_scroll_in_body),
)

# 条数按「编号」去重（第10条有两个子项）；第1..17条 → 17
TOTAL_CHECKS = len({num for num, _, _ in CHECKS})


# ---------------------------------------------------------------- 对外接口
def check_html_detail(path: str) -> list[tuple[str, str, list[str], str]]:
    """逐条检查，返回 [(编号, 检查名, 错误明细, 计量说明)]，供 CLI 打印。"""
    with open(path, encoding="utf-8") as f:
        html = f.read()
    results = []
    for num, name, fn in CHECKS:
        errs, info = fn(html)
        results.append((num, name, errs, info))
    return results


def check_html(path: str) -> list[str]:
    """红项描述列表（`[]` 全绿）。每条格式：`第N条 <检查名>：<细节>`。"""
    return [f"{num} {name}：{detail}"
            for num, name, errs, _ in check_html_detail(path)
            for detail in errs]


def _safe_print(*parts) -> None:
    """GBK 控制台安全打印：中文正常输出，emoji 无法编码时降级为 '?' 而不抛异常。"""
    line = " ".join(str(p) for p in parts)
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "utf-8"
        print(line.encode(enc, "replace").decode(enc, "replace"))


def main(argv: list[str] | None = None) -> int:
    """CLI：全绿退 0，有红项退 1，用法/文件错误退 2。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        _safe_print("用法：python validate_html.py <html文件>")
        return 2
    path = args[0]
    if not os.path.isfile(path):
        _safe_print(f"文件不存在：{path}")
        return 2

    results = check_html_detail(path)
    red = 0
    for num, name, errs, info in results:
        if errs:
            red += 1
            for detail in errs:
                _safe_print(f"❌ {num} {name}：{detail}")
        else:
            _safe_print(f"✅ {num} {name}（{info}）")
    if red:
        _safe_print(f"❌ 终验未通过：{red} 项红(共 {TOTAL_CHECKS} 条编号检查/{len(results)} 项断言行)")
        return 1
    _safe_print(f"✅ {TOTAL_CHECKS} 条终验全绿：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
