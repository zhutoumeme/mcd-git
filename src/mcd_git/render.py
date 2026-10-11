"""终端渲染：git 风格的配色、对齐（中文占两格）、终端二维码。"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence
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

# fancy（--qr fancy）麦当劳主题码：麦门金配色 + 正中像素汉堡 Logo，纠错 H 盖住 Logo 仍可识别。
_QR_GOLD = "#FFBC0D"  # 麦门金：深色模块
_QR_RED = "#DA291C"  # 麦当劳红：Logo 隔离边 / 底
# fancy 正中 7x7 汉堡图案（四周再由代码补 1 圈红边 → 实际 9x9）；0=红底，1=金色笔画。
# 三层：上包-肉-下包，与 fancy-prototype.py 的 BURGER、burger-approved.svg 一致。
_BURGER = [
    [0, 0, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 1, 1, 0],
    [1, 1, 1, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0],
    [1, 1, 1, 1, 1, 1, 1],
    [0, 1, 1, 1, 1, 1, 0],
    [0, 0, 0, 0, 0, 0, 0],
]
_FANCY_QUIET = 2  # fancy 静区（和整格一致）：白色静区是画出来的格子，2 个模块够

_PAY_HOSTS = ("m.mcd.cn",)
_ORDER_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")


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


def pay_block(console: Console, pay_url: str, *, demo: bool = False, big_qr: bool = False, style: str = "half") -> None:
    """push / show 共用：二维码 + 支付链接 + 怎么付的提示。

    二维码优先画「扫了拉起麦当劳 App 付款」的那条链接（和收银页里的二维码一样）；认不出就退回支付链接本身。
    默认半块、紧凑；big_qr（`--qr full`）时画更大的整格二维码，适合半块扫不出的终端；
    style="fancy"（`--qr fancy`）画麦当劳主题码，终端够宽成功时提示「和普通码一样扫」、太窄退回普通码并提示。
    """
    app_url = app_pay_url(pay_url)
    data = app_url or pay_url
    fancy = style == "fancy"
    console.print(qr_text(data, color=console.color_system is not None, width=console.width, big=big_qr, fancy=fancy), no_wrap=True, crop=False)
    console.print(Text.assemble("支付链接：", (pay_url, link(pay_url))))
    if demo:
        console.print("（demo：演示链接，不会扣款）", style=DIM)
    if app_url:
        tips = ["用手机扫上面的二维码，会直接打开麦当劳 App 付款（mcd-git 不会替你付款）",
                "扫不出来：在浏览器里打开支付链接，或到麦当劳 App 订单页付款"]
    else:
        tips = ["用手机扫码或打开麦当劳 App 完成支付（mcd-git 不会替你付款）",
                "扫不出来就直接打开上面的支付链接，或到麦当劳 App 订单页付款"]
    if fancy and console.color_system is not None:
        # 阈值单一来源：和 qr_text 里一样用 _FANCY_QUIET + high=True 的边长判断是否真的画成了 fancy
        n = len(qr_matrix(data, _FANCY_QUIET, high=True))
        if console.width is None or console.width >= n * 2:
            tips.insert(0, "麦当劳主题码，和普通码一样扫")
        else:
            tips.insert(0, "终端太窄，麦当劳主题码已退回普通二维码")
    hints(console, tips)


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


def _fancy_lines(matrix: List[List[bool]]) -> List[Text]:
    """整格画法 + 正中 9x9 汉堡 Logo：深色模块金、浅色白；Logo 区外圈红、内 7x7 按 _BURGER。"""
    n = len(matrix)
    size = len(_BURGER) + 2  # 7 + 四周 1 圈红边 = 9
    lo = (n - size) // 2
    region = {}
    for r in range(size):
        for c in range(size):
            inner = 1 <= r <= len(_BURGER) and 1 <= c <= len(_BURGER)
            gold = inner and _BURGER[r - 1][c - 1]
            region[(lo + r, lo + c)] = _QR_GOLD if gold else _QR_RED
    rows: List[Text] = []
    for y in range(n):
        line = Text()
        for x in range(n):
            color = region.get((y, x)) or (_QR_GOLD if matrix[y][x] else _QR_LIGHT)
            line.append("  ", style="on " + color)
        rows.append(line)
    return rows


def qr_mode(modules: int, width: Optional[int], color: bool, big: bool = False) -> str:
    """选画法：'half' 上下半块「▀」+ 前景 / 背景色（一行两个模块行，默认，最省地方）；
    'full' 每个模块两个空格 + 背景色（一行一个模块行，不依赖字形，`--qr full` 时用，适合半块扫不出的终端）；
    'plain' 没颜色时的 qrencode 风格。modules 是含静区的边长。
    big 为真且终端够宽（width ≥ 边长 × 2）才用 full；否则一律 half（没颜色是 plain）。"""
    if not color:
        return "plain"
    if big and width is not None and width >= modules * 2:
        return "full"
    return "half"


def qr_text(data: str, *, color: bool = True, border: int = QR_QUIET, width: Optional[int] = None, big: bool = False, fancy: bool = False) -> Text:
    """画终端二维码。

    默认半块「▀」画法：一个字符放上下两个模块，高度减半，是终端里最紧凑的画法（约 17 行高），手机能扫。
    `big=True`（命令行 `--qr full`）且终端够宽时，每个模块画成两个带背景色的空格、不依赖字形，整码更大更清楚，
    适合半块在某些终端扫不出的情况。显式指定黑白色，深浅主题和 SVG 导出都能扫。
    `fancy=True`（命令行 `--qr fancy`）且有颜色时画麦当劳主题码：麦门金配色 + 正中像素汉堡 Logo、纠错 H，
    固定整格（不受 big/width 放大影响）；终端太窄（width < 边长×2）时退回普通 half 画法（不画 Logo）。
    没颜色（管道、NO_COLOR）时按 qrencode -t UTF8 的习惯，用方块画浅色模块，适合深色背景终端。
    """
    if fancy and color:
        matrix = qr_matrix(data, _FANCY_QUIET, high=True)
        n = len(matrix)
        if width is None or width >= n * 2:
            return Text("\n").join(_fancy_lines(matrix))
        # 终端太窄：退回普通 half（不画 Logo），由调用方给退回提示
        fancy = False
    size_full = len(qr_matrix(data, QR_QUIET_FULL))
    mode = qr_mode(size_full, width, color, big)
    if mode == "full":
        matrix = qr_matrix(data, QR_QUIET_FULL)  # 整格画法用窄静区：白色静区是画出来的，不用留 4
    else:
        matrix = qr_matrix(data, border)
    size = len(matrix[0]) if matrix else 0
    if mode == "half" and width is not None and width < size and border > _QR_QUIET_MIN:
        matrix = qr_matrix(data, _QR_QUIET_MIN)
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
