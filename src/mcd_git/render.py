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


def qr_text(data: str, *, color: bool = True, border: int = 2) -> Text:
    """用上下半块字符画二维码。

    有颜色时显式指定黑白前景/背景色，深浅主题和 SVG 导出都能扫；
    没颜色（管道、NO_COLOR）时按 qrencode -t UTF8 的习惯，用方块画浅色模块，适合深色背景终端。
    """
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    matrix: List[List[bool]] = qr.get_matrix()
    width = len(matrix[0]) if matrix else 0
    text = Text()
    for y in range(0, len(matrix), 2):
        top = matrix[y]
        bottom = matrix[y + 1] if y + 1 < len(matrix) else [False] * width
        for t, b in zip(top, bottom):
            if color:
                fg = "#000000" if t else "#ffffff"
                bg = "#000000" if b else "#ffffff"
                text.append("▀", style=f"{fg} on {bg}")
            else:
                text.append(_HALF_BLOCKS[(t, b)])
        text.append("\n")
    text.rstrip()
    return text
