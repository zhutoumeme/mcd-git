"""git mcd card --style skyline（默认）：麦门天际线。等距 3D 的 53 周 × 7 天，每天吃了几次就叠几层汉堡。

- 每一天是一块地砖；一层汉堡从下到上是底面包、肉饼、芝士、生菜、顶面包，最上面一层的顶面画芝麻。
  超过 4 层只画 4 层，顶上加一个小「+」；没有订单的日子只有一块暗色地砖。10-24 那格插一面「0x400」小旗。
- 一层汉堡、顶面、地砖、「+」、小旗都只在 <defs> 里定义一次，再用 <use href="#…"> 按位置引用（只有文档内部引用），
  所以一年的图也只有二三十 KB。坐标全是整数，输出逐字节确定。
- 动画是纯 CSS：图片加载时按周从左到右依次落下，只播一次，总时长不超过 3 秒，
  animation-fill-mode: both 停在完整状态；prefers-reduced-motion: reduce 时直接显示完成状态。
- 默认深色夜景，prefers-color-scheme: light 换浅色奶油底（汉堡颜色不变）。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Tuple

from . import render as R
from .money import fmt

WIDTH, HEIGHT = 1200, 624
WX, WY, DX, DY = 18, 4, -9, 7  # 等距投影：沿周（向右下）、沿星期几（向左下）各走一格的屏幕位移
OX, OY = 150, 215  # 第 0 周周一那块地砖的投影原点
GAP, SIDE = 0.12, 0.76  # 地砖之间留缝；每块地砖占一格里的 [GAP, GAP + SIDE]
LAYER = 14  # 一层汉堡的高度（像素）
MAX_LAYERS = 4
STEP_DELAY = 0.04  # 每往右一周晚 0.04 秒落下：52 周 × 0.04 + 0.6 = 2.68 秒
DURATION = 0.6
FLAG_DELAY = 0.2  # 小旗在那一周落下之后再出现，最晚 2.88 秒
LEFT = 60

# 一层汉堡从下到上：（高度占比，正面色，侧面色）
SLICES = (
    (0.22, "#E9B66B", "#C9914A"),  # 底面包
    (0.26, "#8A4B2A", "#6E3A20"),  # 肉饼
    (0.10, "#FFD24A", "#E5B42E"),  # 芝士
    (0.10, "#7DBE4E", "#5E9C38"),  # 生菜
    (0.32, "#F2B95C", "#D39A41"),  # 顶面包
)

_MONO = 'ui-monospace,"SF Mono",Menlo,Consolas,"PingFang SC","Microsoft YaHei","Noto Sans Mono CJK SC",monospace'

_CSS = (
    '\nsvg{font-family:-apple-system,"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}\n'
    f".mo{{font-family:{_MONO};white-space:pre}}\n"
    ".h{font-size:30px;font-weight:800}.s{font-size:14px}.k{font-size:13px}.v{font-size:20px;font-weight:700}.m{font-size:11px}\n"
    ".t1{fill:#F4EFE6}.t2{fill:#9AA0B4}.bd{stroke:#2C3142;stroke-width:1}.tl{fill:#262A38}.tm{fill:#5A2E2B}"
    ".g0{stop-color:#3A2E2A}.g1{stop-color:#161925}.pole{stroke:#E8E3D8}\n"
    f".f{{animation:drop {DURATION}s cubic-bezier(.3,1.25,.5,1) both}}\n"
    "@keyframes drop{0%{transform:translateY(-90px);opacity:0}35%{opacity:1}100%{transform:translateY(0);opacity:1}}\n"
    "@media (prefers-reduced-motion:reduce){.f{animation:none}}\n"
    "@media (prefers-color-scheme:light){\n"
    ".t1{fill:#3B2A1A}.t2{fill:#8A7560}.bd{stroke:#E6D6BD}.tl{fill:#E8DAC2}.tm{fill:#F2C3B0}"
    ".g0{stop-color:#FFF8EC}.g1{stop-color:#F3E6CF}.pole{stroke:#6B4A2E}\n"
    "}\n"
)


def _p(w: float, d: float, h: float = 0.0) -> Tuple[float, float]:
    """格子坐标 → 相对投影原点的屏幕坐标。"""
    return (w * WX + d * DX, w * WY + d * DY - h)


def _pts(points: List[Tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _defs() -> str:
    a, b = GAP, GAP + SIDE
    tile = f'<polygon id="tile" points="{_pts([_p(a, a), _p(b, a), _p(b, b), _p(a, b)])}"/>'
    burger = []
    h0 = 0.0
    for frac, front, side in SLICES:
        h1 = h0 + frac * LAYER
        burger.append(f'<polygon fill="{front}" points="{_pts([_p(a, b, h0), _p(b, b, h0), _p(b, b, h1), _p(a, b, h1)])}"/>')
        burger.append(f'<polygon fill="{side}" points="{_pts([_p(b, a, h0), _p(b, b, h0), _p(b, b, h1), _p(b, a, h1)])}"/>')
        h0 = h1
    cx, cy = _p(GAP + SIDE / 2, GAP + SIDE / 2)
    sesame = "".join(
        f'<ellipse cx="{cx + dx:.1f}" cy="{cy + dy:.1f}" rx="1.1" ry="0.7" fill="#FFF6E0"/>' for dx, dy in ((-3.5, 0.5), (2.5, -1.2), (0.5, 2.2), (4.5, 1.8))
    )
    cap = f'<g id="c"><polygon fill="#F7C873" points="{_pts([_p(a, a), _p(b, a), _p(b, b), _p(a, b)])}"/>{sesame}</g>'
    plus = (
        '<g id="p"><circle r="6" fill="#E8473C" stroke="#FFF6E0" stroke-width="1"/>'
        '<path d="M-1,-3.5h2v2.5h2.5v2h-2.5v2.5h-2v-2.5h-2.5v-2h2.5z" fill="#FFF6E0"/></g>'
    )
    flag = (
        '<g id="fl"><line class="pole" x1="0" y1="0" x2="0" y2="-42" stroke-width="1.5"/>'
        '<path d="M0,-42h44l-6,7.5l6,7.5h-44z" fill="#E8473C"/>'
        f'<text x="5" y="-31" font-family="{html.escape(_MONO)}" font-size="10" font-weight="700" fill="#FFFFFF">0x400</text></g>'
    )
    gradient = '<radialGradient id="g" cx="0.55" cy="0.62" r="0.65"><stop offset="0" class="g0"/><stop offset="1" class="g1"/></radialGradient>'
    return "<defs>" + gradient + tile + f'<g id="b">{"".join(burger)}</g>' + cap + plus + flag + "</defs>"


def _origin(col: int, row: int) -> Tuple[int, int]:
    return OX + col * WX + row * DX, OY + col * WY + row * DY


def _top_center(col: int, row: int, layers: int) -> Tuple[int, int]:
    x, y = _origin(col, row)
    cx, cy = _p(GAP + SIDE / 2, GAP + SIDE / 2)
    return int(round(x + cx)), int(round(y + cy)) - layers * LAYER


def _esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def _text(x: int, y: int, cls: str, content: str, anchor: Optional[str] = None) -> str:
    extra = f' text-anchor="{anchor}"' if anchor else ""
    return f'<text class="{cls}" x="{x}" y="{y}"{extra}>{_esc(content)}</text>'


def _delay(col: int, extra: float = 0.0) -> str:
    return f"{col * STEP_DELAY + extra:.2f}s"


def tiles(model: Dict[str, Any]) -> List[Tuple[str, str, Optional[str]]]:
    """底部统计：（标签，值，值下面的小字说明）。"""
    top_item = model["topItem"]
    top_slot = model["topSlot"]
    out: List[Tuple[str, str, Optional[str]]] = [
        ("过去一年", f"{model['total']} 次", None),
        ("最常点", top_item["name"] if top_item else "—", None),
        ("最常时段", f"{top_slot['slot']} · {top_slot['label']}" if top_slot else "—", None),
    ]
    if model["savings"] is not None:
        out.append(("用券省下", fmt(model["savings"]), savings_note(model)))
    out.append(("10-24", "Happy 0x400 Day", None))
    return out


def savings_note(model: Dict[str, Any]) -> Optional[str]:
    """order-list 不给优惠明细：只有 push / show 过的单知道省了多少，所以省下的钱是下限，要交代清楚。"""
    if model["savings"] is None:
        return None
    known, total = model["savingsKnownOrders"] or 0, model["total"]
    if known >= total:
        return None
    return f"只算有优惠明细的 {known} 单，是下限"


def render_svg(model: Dict[str, Any]) -> str:
    title = f"麦门天际线：过去一年吃麦 {model['total']} 次"
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="t d">',
        f'<title id="t">{_esc(title)}</title>',
        f'<desc id="d">{_esc(model["desc"] + "每天吃了几次就叠几层汉堡，10-24 插了一面 0x400 小旗。")}</desc>',
        "<!-- 0x400 = 1024 -->",
        f"<style>{_CSS}</style>",
        _defs(),
        f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" rx="18" fill="url(#g)"/>',
        _text(LEFT, 64, "t1 h", "麦门天际线"),
        _text(LEFT, 94, "t2 s mo", "$ git mcd card  # 每天吃几次，就叠几层汉堡"),
    ]
    weeks = model["weeks"]
    # 地砖先全部画完（都在地面上，压不住任何汉堡），再按周从左到右、每周从周一到周日画汉堡：等距投影下这就是画家算法的顺序
    floor = ['<g class="tl">']
    mark: Optional[Tuple[int, int, int]] = None
    for col, column in enumerate(weeks):
        for row, cell in enumerate(column):
            if cell is None:
                continue
            x, y = _origin(col, row)
            floor.append(f'<use href="#tile" x="{x}" y="{y}"' + (' class="tm"' if cell["mark"] else "") + "/>")
            if cell["mark"]:
                mark = (col, row, min(cell["count"], MAX_LAYERS))
    floor.append("</g>")
    out += floor
    for col, column in enumerate(weeks):
        stacks = []
        for row, cell in enumerate(column):
            if cell is None or not cell["count"]:
                continue
            x, y = _origin(col, row)
            layers = min(cell["count"], MAX_LAYERS)
            stacks += [f'<use href="#b" x="{x}" y="{y - i * LAYER}"/>' for i in range(layers)]
            stacks.append(f'<use href="#c" x="{x}" y="{y - layers * LAYER}"/>')
            if cell["count"] > MAX_LAYERS:
                px, py = _top_center(col, row, layers)
                stacks.append(f'<use class="more" href="#p" x="{px}" y="{py - 10}"/>')
        if stacks:
            out.append(f'<g class="f" style="animation-delay:{_delay(col)}">' + "".join(stacks) + "</g>")
    if mark is not None:
        col, row, layers = mark
        fx, fy = _top_center(col, row, layers)
        if layers and weeks[col][row]["count"] > MAX_LAYERS:
            fy -= 16  # 让开「+」
        out.append(f'<g class="f flag" style="animation-delay:{_delay(col, FLAG_DELAY)}"><use href="#fl" x="{fx}" y="{fy}"/></g>')

    out.append(f'<line class="bd" x1="{LEFT}" y1="510" x2="{WIDTH - LEFT}" y2="510"/>')
    items = tiles(model)
    width = (WIDTH - 2 * LEFT) // len(items)
    for i, (label, value, note) in enumerate(items):
        x = LEFT + i * width
        out.append(_text(x, 540, "t2 k", label))
        out.append(_text(x, 568, "t1 v", R.clip(value, max(8, (width - 16) // 11))))
        if note:
            out.append(_text(x, 586, "t2 m", note))
    out.append(_text(LEFT, 606, "t2 m", model["footer"]))
    out.append(_text(WIDTH - LEFT, 606, "t2 m", "1 层汉堡 = 吃 1 次 · 超过 4 次只叠 4 层，顶上标 +", anchor="end"))
    out.append("</svg>")
    return "\n".join(out) + "\n"
