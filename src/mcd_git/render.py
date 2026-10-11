"""终端渲染：git 风格的配色、对齐（中文占两格）、终端二维码。"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

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

QR_QUIET = 4  # 二维码规范要求的静区（模块数）；深色终端背景上静区太窄，手机常常认不出边界
_QR_QUIET_MIN = 2  # 终端窄到连半块画法都放不下时退回的静区（0.2.0 的老样子）
_QR_DARK = "#000000"
_QR_LIGHT = "#ffffff"


def qr_matrix(data: str, border: int = QR_QUIET) -> List[List[bool]]:
    """二维码模块矩阵（含静区），True 是深色模块。"""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def qr_mode(modules: int, width: Optional[int], color: bool) -> str:
    """选画法：'full' 每个模块两个空格 + 背景色（一行一个模块行，不依赖字形）；
    'half' 上下半块「▀」+ 前景 / 背景色（一行两个模块行）；'plain' 没颜色时的 qrencode 风格。
    modules 是含静区的边长。"""
    if not color:
        return "plain"
    if width is not None and width >= modules * 2:
        return "full"
    return "half"


def qr_text(data: str, *, color: bool = True, border: int = QR_QUIET, width: Optional[int] = None) -> Text:
    """画终端二维码。

    终端够宽（width ≥ 边长 × 2）且有颜色时，每个模块画成两个带背景色的空格：模块铺满整个字符格，
    不受字体半块字形高度、行距留缝的影响，手机最好扫；不够宽才退回上下半块「▀」画法
    （连边长都放不下时静区退回 2）。显式指定黑白色，深浅主题和 SVG 导出都能扫。
    没颜色（管道、NO_COLOR）时按 qrencode -t UTF8 的习惯，用方块画浅色模块，适合深色背景终端。
    """
    matrix = qr_matrix(data, border)
    size = len(matrix[0]) if matrix else 0
    mode = qr_mode(size, width, color)
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
