"""0.2.1 终端二维码（plan.md T24～T27）：静区 4、宽终端整格背景色画法、窄终端退回半块。
链接是合成的：和真实支付链接同形态（https://m.mcd.cn/mcp/scanToPay?orderId= + 28 位数字，67 字节）。
"""
from __future__ import annotations

import io
import os
from typing import List, Tuple

import qrcode
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from mcd_git import render as R
from mcd_git.cli import main

from .conftest import FIXED_NOW, Runner

URL = "https://m.mcd.cn/mcp/scanToPay?orderId=" + "1024" * 7
DARK = "000000"
LIGHT = "ffffff"
_CONSOLE = Console(width=200, color_system="truecolor", force_terminal=True)


def _bare_matrix(data: str) -> List[List[bool]]:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=0)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def _hex(color: object) -> str:
    return color.get_truecolor().hex.lstrip("#")  # type: ignore[attr-defined]


def _decode_full(text: Text) -> List[List[bool]]:
    rows = []
    for line in text.split("\n"):
        assert set(line.plain) == {" "}
        row = []
        for col in range(0, len(line.plain), 2):
            style = line.get_style_at_offset(_CONSOLE, col)
            assert line.get_style_at_offset(_CONSOLE, col + 1) == style  # 一个模块两格同色
            row.append({DARK: True, LIGHT: False}[_hex(style.bgcolor)])
        rows.append(row)
    return rows


def _decode_half(text: Text) -> List[List[bool]]:
    rows: List[List[bool]] = []
    for line in text.split("\n"):
        assert set(line.plain) == {"▀"}
        top, bottom = [], []
        for col in range(len(line.plain)):
            style = line.get_style_at_offset(_CONSOLE, col)
            top.append({DARK: True, LIGHT: False}[_hex(style.color)])
            bottom.append({DARK: True, LIGHT: False}[_hex(style.bgcolor)])
        rows += [top, bottom]
    return rows


def _decode_plain(text: Text) -> List[List[bool]]:
    inverse = {ch: pair for pair, ch in R._HALF_BLOCKS.items()}
    rows: List[List[bool]] = []
    for line in text.plain.split("\n"):
        pairs = [inverse[ch] for ch in line]
        rows += [[t for t, _ in pairs], [b for _, b in pairs]]
    return rows


def _quiet(matrix: List[List[bool]], border: int) -> bool:
    size = len(matrix)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if (y < border or y >= size - border or x < border or x >= size - border) and dark:
                return False
    return True


def _strip(matrix: List[List[bool]], border: int) -> List[List[bool]]:
    return [row[border:-border] for row in matrix[border:-border]]


# ---------------------------------------------------------------- T24 矩阵


def test_qr_matrix_has_quiet_zone_of_four() -> None:
    assert len(URL.encode()) == 67
    matrix = R.qr_matrix(URL)
    assert R.QR_QUIET == 4
    assert len(matrix) == 41 and all(len(row) == 41 for row in matrix)  # version 4（33）+ 2 × 4
    assert _quiet(matrix, 4)
    assert _strip(matrix, 4) == _bare_matrix(URL)


# ---------------------------------------------------------------- T25 整格画法


def test_full_mode_draws_each_module_as_two_background_cells() -> None:
    assert R.qr_mode(41, 82, True) == "full"
    text = R.qr_text(URL, color=True, width=82)
    lines = text.split("\n")
    assert len(lines) == 41
    assert all(cell_len(line.plain) == 82 for line in lines)
    assert _decode_full(text) == R.qr_matrix(URL)


# ---------------------------------------------------------------- T26 半块 / 无颜色


def test_half_mode_when_terminal_is_narrower_than_full() -> None:
    assert R.qr_mode(41, 81, True) == "half"
    assert R.qr_mode(41, None, True) == "half"
    for width in (81, None):
        text = R.qr_text(URL, color=True, width=width)
        lines = text.split("\n")
        assert len(lines) == 21 and all(cell_len(line.plain) == 41 for line in lines)
        decoded = _decode_half(text)
        assert decoded[-1] == [False] * 41  # 最后一行的下半格补浅色
        assert decoded[:41] == R.qr_matrix(URL)


def test_half_mode_falls_back_to_quiet_two_when_even_half_does_not_fit() -> None:
    text = R.qr_text(URL, color=True, width=40)
    lines = text.split("\n")
    assert len(lines) == 19 and all(cell_len(line.plain) == 37 for line in lines)
    assert _decode_half(text)[:37] == R.qr_matrix(URL, 2)


def test_plain_mode_keeps_qrencode_style_with_quiet_four() -> None:
    assert R.qr_mode(41, 200, False) == "plain"
    text = R.qr_text(URL, color=False, width=200)
    assert not text.spans
    lines = text.plain.split("\n")
    assert len(lines) == 21 and all(len(line) == 41 for line in lines)
    assert _decode_plain(text)[:41] == R.qr_matrix(URL)


# ---------------------------------------------------------------- T27 push


def _confirmed_push(runner: Runner) -> List[str]:
    runner.run("--demo", "add", "巨无霸套餐")
    runner.run("--demo", "commit", "-m", "午饭")
    code, data = runner.json("--demo", "push")
    assert code == 0 and data["data"]["status"] == "confirmation_required"
    return ["--demo", "push", "--confirm", data["data"]["confirm"]]


def _color_run(runner: Runner, argv: List[str], width: int) -> Tuple[int, Text]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=width, height=60, highlight=False, markup=False, emoji=False)
    env = dict(os.environ)
    env.update(runner.env_overrides)
    code = main(argv, console=console, env=env, input_fn=lambda p: "", interactive=False, now=FIXED_NOW)
    return code, Text.from_ansi(buf.getvalue())


def test_push_without_color_uses_plain_qr_and_adds_fallback_hint(runner: Runner) -> None:
    code, out = runner.run(*_confirmed_push(runner))
    assert code == 0
    assert "▀" in out or "█" in out
    assert "hint: 扫不出来就直接打开上面的支付链接，或到麦当劳 App 订单页付款" in out
    hints = [line for line in out.splitlines() if line.startswith("hint: ")]
    assert hints[-1] == "hint: 付完运行 git mcd show 查看取餐码"


def test_push_picks_full_on_wide_color_terminal_and_half_on_narrow(runner: Runner) -> None:
    argv = _confirmed_push(runner)
    code, text = _color_run(runner, argv, 120)
    assert code == 0
    blank = [line for line in text.split("\n") if line.plain and set(line.plain) == {" "}]
    assert len(blank) >= 21 + 8 and "▀" not in text.plain  # 整格画法：每个模块行一行，全是带背景色的空格
    assert "扫不出来就直接打开上面的支付链接" in text.plain

    runner.env_overrides["MCD_GIT_HOME"] = str(runner.tmp_path / "narrow")  # 另起一个 demo 状态再下一单
    argv = _confirmed_push(runner)
    code, text = _color_run(runner, argv, 60)
    assert code == 0 and "▀" in text.plain
