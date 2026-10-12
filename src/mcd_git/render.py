"""终端渲染：git 风格的配色、对齐（中文占两格）、终端二维码。"""
from __future__ import annotations

import random
import re
from typing import Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlsplit

import qrcode
from rich.cells import cell_len
from rich.console import Console
from rich.style import Style
from rich.text import Text

# git 的默认配色：哈希黄、HEAD 青、分支绿、远端红
HASH = "yellow"
HEAD = "bold cyan"
BRANCH = "bold green"
REMOTE = "bold red"
TAG = "bold yellow"
ADD = "green"
DEL = "red"
META = "bold"
HUNK = "cyan"
DIM = "dim"
HINT = "yellow"


def link(url: str) -> Style:
    """下划线 + 终端超链接（支持 OSC 8 的终端里可以直接点开）。"""
    return Style(underline=True, link=url)


def pad(text: str, width: int) -> str:
    """按终端显示宽度补空格（中文占两格）。"""
    gap = width - cell_len(text)
    return text + " " * gap if gap > 0 else text


def lpad(text: str, width: int) -> str:
    gap = width - cell_len(text)
    return " " * gap + text if gap > 0 else text


def clip(text: str, width: int) -> str:
    if cell_len(text) <= width:
        return text
    out = ""
    for ch in text:
        if cell_len(out + ch + "…") > width:
            break
        out += ch
    return out + "…"


def short(commit_id: Optional[str]) -> str:
    return (commit_id or "")[:7]


def mask_order_id(order_id: Optional[str]) -> str:
    text = str(order_id or "")
    return f"{text[:4]}…{text[-4:]}" if len(text) > 12 else text


def hints(console: Console, lines: Iterable[str]) -> None:
    for line in lines:
        console.print(Text.assemble(("hint: ", HINT), (line, HINT)))


def decorations(items: Sequence[str]) -> Text:
    """(HEAD -> lunch, origin/lunch, breakfast) 这种装饰。"""
    text = Text("(", style=HASH)
    for i, item in enumerate(items):
        if i:
            text.append(", ", style=HASH)
        if item.startswith("HEAD -> "):
            text.append("HEAD -> ", style=HEAD)
            text.append(item[len("HEAD -> "):], style=BRANCH)
        elif item == "HEAD":
            text.append(item, style=HEAD)
        elif item.startswith("origin/"):
            text.append(item, style=REMOTE)
        else:
            text.append(item, style=BRANCH)
    text.append(")", style=HASH)
    return text


_HALF_BLOCKS = {(False, False): "█", (False, True): "▀", (True, False): "▄", (True, True): " "}

QR_QUIET = 4  # 半块 / 无颜色画法的静区（模块数）：这两种画法静区靠字形或终端背景，留宽一点手机才认得出边界
QR_QUIET_FULL = 2  # 整格画法的静区：静区本身就是画出来的白色格子，2 个模块足够，二维码能小一圈
_QR_QUIET_MIN = 2  # 终端窄到连半块画法都放不下时退回的静区（0.2.0 的老样子）
_QR_DARK = "#000000"
_QR_LIGHT = "#ffffff"

# fancy（--qr fancy）麦当劳主题码：麦门金配色 + 正中像素麦当劳元素 Logo，纠错 H 盖住 Logo 仍可识别。
_QR_GOLD = "#FFBC0D"  # 麦门金：深色模块
_QR_RED = "#DA291C"  # 麦当劳红：Logo 隔离边 / 底
# fancy Logo 的 6 个麦当劳元素图案（v3）：每个 9x9，四周再由代码补 1 圈红边 → 实际 11x11。
# 0=红底，1=金色笔画。图案逐行照抄 notes/.../templates-6-prototype.py 的 PATTERNS（已逐个 macOS Vision 验证可识别）。
_FANCY_PATTERNS = {
    "burger": [  # 汉堡：芝麻面包-生菜-肉-下面包
        [0, 0, 1, 1, 1, 1, 1, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 1, 1, 1, 1, 1, 0, 0],
    ],
    "fries": [  # 薯条：红盒(梯形) + 伸出的金色薯条，盒上有斜纹口
        [0, 1, 0, 0, 1, 0, 0, 1, 0],
        [1, 0, 1, 0, 1, 0, 1, 0, 1],
        [1, 0, 1, 0, 1, 0, 1, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 1, 0, 1, 0, 1, 0, 1, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 1, 1, 1, 1, 1, 0, 0],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
    ],
    "nuggets": [  # 麦乐鸡：三块不规则鸡块（圆角团块）
        [0, 0, 1, 1, 0, 0, 0, 0, 0],
        [0, 1, 1, 1, 1, 0, 1, 1, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 1],
        [0, 0, 1, 1, 0, 1, 1, 1, 1],
        [0, 0, 0, 0, 0, 0, 1, 1, 0],
        [0, 1, 1, 0, 0, 0, 0, 0, 0],
        [1, 1, 1, 1, 0, 0, 0, 0, 0],
        [1, 1, 1, 1, 0, 0, 0, 0, 0],
        [0, 1, 1, 0, 0, 0, 0, 0, 0],
    ],
    "cone": [  # 甜筒：顶部漩涡冰淇淋球 + 下方蛋筒三角，收成尖
        [0, 0, 0, 1, 1, 1, 0, 0, 0],
        [0, 0, 1, 1, 1, 1, 1, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 1, 1, 1, 1, 1, 0, 0],
        [0, 0, 0, 1, 1, 1, 0, 0, 0],
        [0, 0, 0, 1, 1, 1, 0, 0, 0],
        [0, 0, 0, 0, 1, 0, 0, 0, 0],
        [0, 0, 0, 0, 1, 0, 0, 0, 0],
    ],
    "pie": [  # 派：半圆酥皮
        [0, 0, 0, 1, 1, 1, 0, 0, 0],
        [0, 0, 1, 1, 1, 1, 1, 0, 0],
        [0, 1, 1, 1, 1, 1, 1, 1, 0],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
    ],
    "arch": [  # 圆润金拱门 M：三道弧峰 + 底部两腿，仿官方曲线
        [0, 0, 0, 0, 0, 0, 0, 0, 0],
        [0, 1, 1, 0, 0, 0, 1, 1, 0],
        [1, 1, 1, 1, 0, 1, 1, 1, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 1, 0, 1, 1, 1, 0, 1, 1],
        [1, 1, 0, 0, 1, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 0, 0, 1, 1],
        [1, 1, 0, 0, 1, 0, 0, 1, 1],
    ],
}
# 可选模板名（固定顺序：随机复现、报错列名、文档都用这个顺序）
FANCY_TEMPLATES = ("burger", "fries", "nuggets", "cone", "pie", "arch")
# 给用户看的中文名（底部提示里带上当前是哪个图案）
FANCY_LABELS = {"burger": "汉堡", "fries": "薯条", "nuggets": "麦乐鸡", "cone": "甜筒", "pie": "派", "arch": "金拱门"}
_FANCY_QUIET = 2  # fancy 静区（和整格一致）：白色静区是画出来的格子，2 个模块够
# `--qr` 画法（单一来源：校验、帮助、补全脚本都用它）。full 整格是默认：不依赖字形，各终端都扫得出
QR_STYLES = ("full", "half", "fancy")
QR_STYLE_VALUES = QR_STYLES + tuple("fancy=" + t for t in FANCY_TEMPLATES)

_PAY_HOSTS = ("m.mcd.cn",)
_ORDER_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")

_FANCY_ENV = "MCD_FANCY_TEMPLATE"  # 测试 / 截图固定图案用的环境变量（取值须是 FANCY_TEMPLATES 之一）


class FancyTemplateError(ValueError):
    """`--qr fancy=<名字>` 指定了不存在的模板。"""


def parse_qr_style(raw: Optional[str]) -> Tuple[str, Optional[str]]:
    """解析 `--qr` 取值：full（默认）/ half / fancy / fancy=名字（也接受 fancy:名字）。

    返回 (mode, template)：mode 是 full/half/fancy；template 只在 fancy 指定了名字时非空，否则 None（随机）。
    名字非法时抛 FancyTemplateError，并在消息里列出可选名。
    """
    text = (raw or "full").strip()
    for sep in ("=", ":"):
        if text.startswith("fancy" + sep):
            name = text[len("fancy") + 1:].strip().lower()
            if name not in _FANCY_PATTERNS:
                raise FancyTemplateError(
                    f"没有叫 '{name}' 的麦当劳主题码模板；可选：{'、'.join(FANCY_TEMPLATES)}")
            return "fancy", name
    return text, None


def resolve_fancy_template(
    template: Optional[str] = None,
    *,
    rng: Optional[random.Random] = None,
    env: Optional[dict] = None,
) -> str:
    """定下这次 fancy 用哪个图案。

    指定了 template（已由 parse_qr_style 校验过）就用它；否则看环境变量 MCD_FANCY_TEMPLATE（测试 / 截图复现用）；
    都没有就从 FANCY_TEMPLATES 里随机挑一个（rng 可注入，保证测试可复现、能覆盖到每个模板）。
    """
    if template is not None:
        if template not in _FANCY_PATTERNS:
            raise FancyTemplateError(
                f"没有叫 '{template}' 的麦当劳主题码模板；可选：{'、'.join(FANCY_TEMPLATES)}")
        return template
    pinned = (env or {}).get(_FANCY_ENV)
    if pinned:
        pinned = pinned.strip().lower()
        if pinned in _FANCY_PATTERNS:
            return pinned
    chooser = rng or random
    return chooser.choice(list(FANCY_TEMPLATES))


def app_pay_url(pay_url: Optional[str]) -> Optional[str]:
    """把 payH5Url（https://m.mcd.cn/mcp/scanToPay?orderId=…）换成麦当劳收银页里那个二维码的内容。

    浏览器打开 scanToPay，页面里画的二维码编码的是同域的 /mcp/jumpToApp?orderId=…（ECS 订单再带
    &orderCategory=ECS），手机扫了直接拉起麦当劳 App 付款；这里照着页面的规则拼出同一条链接，
    终端里的二维码就和网页里的一样。认不出的链接（demo、以后接口改版）返回 None，调用方退回原链接。
    """
    if not pay_url:
        return None
    try:
        parts = urlsplit(pay_url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname not in _PAY_HOSTS or parts.path.rstrip("/") != "/mcp/scanToPay":
        return None
    query = parse_qs(parts.query)
    order_id = (query.get("orderId") or [""])[0]
    if not _ORDER_ID.fullmatch(order_id):
        return None
    url = f"https://{parts.hostname}/mcp/jumpToApp?orderId={order_id}"
    if (query.get("orderCategory") or [""])[0] == "ECS":
        url += "&orderCategory=ECS"
    return url


def pay_block(
    console: Console,
    pay_url: str,
    *,
    demo: bool = False,
    style: str = "full",
    rng: Optional[random.Random] = None,
    env: Optional[dict] = None,
) -> None:
    """push / show 共用：二维码 + 支付链接 + 怎么付的提示。

    二维码优先画「扫了拉起麦当劳 App 付款」的那条链接（和收银页里的二维码一样）；认不出就退回支付链接本身。
    style 是 `--qr` 的取值：默认 full 整格（各终端都扫得出），终端放不下整格时自动退回半块并提示原因；
    half 更紧凑，但部分终端（如 macOS Terminal.app）可能扫不出（显式 half 不出退回提示）；
    "fancy" 或 "fancy=名字" 画麦当劳主题码：6 个麦当劳元素模板随机出一个（或指定一个），
    终端够宽成功时提示当前是哪个图案、太窄退回普通码并提示。
    rng/env 可注入，让随机图案在测试 / 截图里固定复现。
    """
    mode, template = parse_qr_style(style)
    chosen = resolve_fancy_template(template, rng=rng, env=env) if mode == "fancy" else "burger"
    app_url = app_pay_url(pay_url)
    data = app_url or pay_url
    drawn, border, fallback = qr_layout(data, color=console.color_system is not None, width=console.width, style=mode)
    console.print(_draw_qr(data, drawn, border, chosen), no_wrap=True, crop=False)
    console.print(Text.assemble("支付链接：", (pay_url, link(pay_url))))
    if demo:
        console.print("（demo：演示链接，不会扣款）", style=DIM)
    if app_url:
        tips = ["用手机扫上面的二维码，会直接打开麦当劳 App 付款（mcd-git 不会替你付款）",
                "扫不出来：在浏览器里打开支付链接，或到麦当劳 App 订单页付款"]
    else:
        tips = ["用手机扫码或打开麦当劳 App 完成支付（mcd-git 不会替你付款）",
                "扫不出来就直接打开上面的支付链接，或到麦当劳 App 订单页付款"]
    notices: List[str] = []
    if drawn == "fancy":
        notices.append(f"麦当劳主题码·{FANCY_LABELS[chosen]}，和普通码一样扫")
    elif fallback and "fancy" in fallback:
        notices.append("终端太窄，麦当劳主题码已退回普通二维码")
    if fallback and "full" in fallback:
        need = len(qr_matrix(data, QR_QUIET_FULL)) * 2
        notices.append(
            f"终端只有 {console.width} 列，放不下整格二维码（要 {need} 列），已退回更紧凑的半块画法；"
            "部分终端（如 macOS Terminal.app）半块可能扫不出，把窗口拉宽后再运行 git mcd show 重新出码")
    hints(console, notices + tips)


def qr_matrix(data: str, border: int = QR_QUIET, *, high: bool = False) -> List[List[bool]]:
    """二维码模块矩阵（含静区），True 是深色模块。

    默认纠错等级 M：终端半块字形有缝、复制时也容易被破坏，留点容错更稳。
    high=True 时用纠错 H（fancy 主题码用）：30% 冗余足以盖住正中 9×9 汉堡 Logo 区仍可识别。
    """
    ec = qrcode.constants.ERROR_CORRECT_H if high else qrcode.constants.ERROR_CORRECT_M
    qr = qrcode.QRCode(error_correction=ec, box_size=1, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def _fancy_lines(matrix: List[List[bool]], pattern: List[List[int]]) -> List[Text]:
    """整格画法 + 正中 11x11 麦当劳元素 Logo：深色模块金、浅色白；Logo 区外圈红、内 9x9 按 pattern。"""
    n = len(matrix)
    size = len(pattern) + 2  # 9 + 四周 1 圈红边 = 11
    lo = (n - size) // 2
    region = {}
    for r in range(size):
        for c in range(size):
            inner = 1 <= r <= len(pattern) and 1 <= c <= len(pattern)
            gold = inner and pattern[r - 1][c - 1]
            region[(lo + r, lo + c)] = _QR_GOLD if gold else _QR_RED
    rows: List[Text] = []
    for y in range(n):
        line = Text()
        for x in range(n):
            color = region.get((y, x)) or (_QR_GOLD if matrix[y][x] else _QR_LIGHT)
            line.append("  ", style="on " + color)
        rows.append(line)
    return rows


def qr_layout(data: str, *, color: bool, width: Optional[int], style: str = "full") -> Tuple[str, int, Optional[str]]:
    """二维码画法决策（单一来源，qr_text 和 pay_block 都用它）：返回 (mode, border, fallback)。

    mode 是实际画法：'fancy' 麦当劳主题码 / 'full' 每个模块两个带背景色的空格（默认，不依赖字形）/
    'half' 上下半块「▀」+ 前景 / 背景色（一行两个模块行，最紧凑）/ 'plain' 没颜色时的 qrencode 风格。
    border 是静区模块数；fallback 记从哪种请求画法退回：None / 'fancy' / 'full' / 'fancy+full'。
    规则：没颜色一律 plain（fancy 也不生效）；fancy 放不下（width < 边长×2）先退 full；full 放不下退 half；
    half 连静区 4 都放不下（width < 边长）时退到静区 2。width 为 None 表示不限宽。
    """
    if style not in QR_STYLES:
        raise ValueError(f"未知二维码画法：{style!r}")
    if not color:
        return "plain", QR_QUIET, None
    fallback: Optional[str] = None
    if style == "fancy":
        n = len(qr_matrix(data, _FANCY_QUIET, high=True))
        if width is None or width >= n * 2:
            return "fancy", _FANCY_QUIET, None
        fallback, style = "fancy", "full"
    if style == "full":
        n = len(qr_matrix(data, QR_QUIET_FULL))  # 整格画法用窄静区：白色静区是画出来的，不用留 4
        if width is None or width >= n * 2:
            return "full", QR_QUIET_FULL, fallback
        fallback = "fancy+full" if fallback else "full"
    size = len(qr_matrix(data, QR_QUIET))
    border = _QR_QUIET_MIN if width is not None and width < size else QR_QUIET
    return "half", border, fallback


def qr_text(data: str, *, color: bool = True, width: Optional[int] = None, style: str = "full", template: str = "burger") -> Text:
    """画终端二维码，style 只接受 'full' / 'half' / 'fancy'，实际画法由 qr_layout 决定。

    默认 full 整格：每个模块画成两个带背景色的空格、不依赖字形，各终端都扫得出；终端放不下时自动退回半块。
    half 半块「▀」：一个字符放上下两个模块，高度减半，更紧凑，但部分终端（如 macOS Terminal.app）可能扫不出。
    fancy 麦当劳主题码：麦门金配色 + 正中像素 Logo（template 指定 6 个麦当劳元素中的哪个，默认 burger）、纠错 H；
    终端太窄时先退回整格、再不行退回半块（不画 Logo）。显式指定黑白色，深浅主题和 SVG 导出都能扫。
    没颜色（管道、NO_COLOR）时按 qrencode -t UTF8 的习惯，用方块画浅色模块，适合深色背景终端。
    """
    mode, border, _ = qr_layout(data, color=color, width=width, style=style)
    return _draw_qr(data, mode, border, template)


def _draw_qr(data: str, mode: str, border: int, template: str) -> Text:
    """按 qr_layout 定下的画法和静区出图。"""
    if mode == "fancy":
        return Text("\n").join(_fancy_lines(qr_matrix(data, border, high=True), _FANCY_PATTERNS[template]))
    matrix = qr_matrix(data, border)
    size = len(matrix[0]) if matrix else 0
    rows: List[Text] = []
    if mode == "full":
        for row in matrix:
            line = Text()
            for dark in row:
                line.append("  ", style=f"on {_QR_DARK if dark else _QR_LIGHT}")
            rows.append(line)
    else:
        for y in range(0, len(matrix), 2):
            top = matrix[y]
            bottom = matrix[y + 1] if y + 1 < len(matrix) else [False] * size
            line = Text()
            for t, b in zip(top, bottom):
                if mode == "half":
                    fg = _QR_DARK if t else _QR_LIGHT
                    bg = _QR_DARK if b else _QR_LIGHT
                    line.append("▀", style=f"{fg} on {bg}")
                else:
                    line.append(_HALF_BLOCKS[(t, b)])
            rows.append(line)
    return Text("\n").join(rows)
