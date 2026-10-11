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
    assert len(matrix) == 41 and all(len(row) == 41 for row in matrix)  # 纠错 M：version 4（33）+ 2 × 4
    assert _quiet(matrix, 4)
    assert _strip(matrix, 4) == _bare_matrix(URL)


# ---------------------------------------------------------------- T26 默认半块（0.2.3：默认画法，最紧凑）


def test_half_is_the_default_and_most_compact() -> None:
    assert R.qr_mode(37, 200, True, False) == "half"  # 默认 big=False 永远 half
    assert R.qr_mode(37, 200, True) == "half"
    text = R.qr_text(URL, color=True, width=200)  # 不传 big
    lines = text.split("\n")
    assert len(lines) == 21 and all(cell_len(line.plain) == 41 for line in lines)  # 比整格 41×82 小得多
    decoded = _decode_half(text)
    assert decoded[-1] == [False] * 41  # 最后一行的下半格补浅色
    assert decoded[:41] == R.qr_matrix(URL)


def test_half_mode_falls_back_to_quiet_two_when_even_half_does_not_fit() -> None:
    text = R.qr_text(URL, color=True, width=40)
    lines = text.split("\n")
    assert len(lines) == 19 and all(cell_len(line.plain) == 37 for line in lines)
    assert _decode_half(text)[:37] == R.qr_matrix(URL, 2)


# ---------------------------------------------------------------- T25 big=full 显式开大图（静区 2）


def test_big_full_mode_opt_in_draws_each_module_as_two_background_cells() -> None:
    assert R.QR_QUIET_FULL == 2
    assert R.qr_mode(37, 74, True, True) == "full"
    text = R.qr_text(URL, color=True, width=74, big=True)
    lines = text.split("\n")
    assert len(lines) == 37 and all(cell_len(line.plain) == 74 for line in lines)
    assert _decode_full(text) == R.qr_matrix(URL, 2)
    assert _quiet(_decode_full(text), 2)


def test_big_full_falls_back_to_half_when_terminal_too_narrow() -> None:
    assert R.qr_mode(37, 73, True, True) == "half"  # big 但放不下 → 退回 half


def test_plain_mode_keeps_qrencode_style_with_quiet_four() -> None:
    assert R.qr_mode(41, 200, False) == "plain"
    text = R.qr_text(URL, color=False, width=200)
    assert not text.spans
    lines = text.plain.split("\n")
    assert len(lines) == 21 and all(len(line) == 41 for line in lines)
    assert _decode_plain(text)[:41] == R.qr_matrix(URL)


# ---------------------------------------------------------------- 0.2.3：二维码和收银页一样，扫了拉起 App


def test_app_pay_url_matches_the_cashier_page_rule() -> None:
    oid = "1030" + "0" * 24
    assert R.app_pay_url(f"https://m.mcd.cn/mcp/scanToPay?orderId={oid}") == f"https://m.mcd.cn/mcp/jumpToApp?orderId={oid}"
    assert R.app_pay_url(f"https://m.mcd.cn/mcp/scanToPay/?orderId={oid}&orderCategory=ECS") == (
        f"https://m.mcd.cn/mcp/jumpToApp?orderId={oid}&orderCategory=ECS")
    # 认不出的一律 None，调用方退回原链接：demo、别的域名、http、别的路径、坏订单号
    for bad in (None, "", "https://example.com/mcd-git-demo/pay?orderId=1", f"http://m.mcd.cn/mcp/scanToPay?orderId={oid}",
                f"https://m.mcd.cn.evil.com/mcp/scanToPay?orderId={oid}", f"https://m.mcd.cn/mcp/other?orderId={oid}",
                "https://m.mcd.cn/mcp/scanToPay?orderId=", "https://m.mcd.cn/mcp/scanToPay?orderId=a%26b%3D1"):
        assert R.app_pay_url(bad) is None


def test_pay_block_default_half_encodes_app_link_and_keeps_pay_link_text() -> None:
    pay = "https://m.mcd.cn/mcp/scanToPay?orderId=" + "1030" * 7
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
    R.pay_block(console, pay)  # 默认 half
    text = Text.from_ansi(buf.getvalue())
    qr_lines = [line for line in text.split("\n") if "▀" in line.plain]
    assert len(qr_lines) == 21  # 默认半块、静区 4：21 行，而不是整格的 41 行
    assert _decode_half(Text("\n").join(qr_lines))[:41] == R.qr_matrix(R.app_pay_url(pay))  # 二维码是 App 链接
    assert "支付链接：" + pay in text.plain  # 文字链接仍是原支付链接（浏览器打开）
    assert "会直接打开麦当劳 App 付款" in text.plain


def test_pay_block_big_qr_uses_full_mode() -> None:
    pay = "https://m.mcd.cn/mcp/scanToPay?orderId=" + "1030" * 7
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
    R.pay_block(console, pay, big_qr=True)
    text = Text.from_ansi(buf.getvalue())
    qr_lines = [line for line in text.split("\n") if line.plain and set(line.plain) == {" "}]
    assert len(qr_lines) == 37  # 整格画法、静区 2
    assert _decode_full(Text("\n").join(qr_lines)) == R.qr_matrix(R.app_pay_url(pay), 2)


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


def test_push_defaults_to_compact_half_qr(runner: Runner) -> None:
    # 默认（不带 --qr）在宽终端也用半块：紧凑、不占满屏
    argv = _confirmed_push(runner)
    code, text = _color_run(runner, argv, 120)
    assert code == 0 and "▀" in text.plain
    assert "扫不出来" in text.plain


def test_push_qr_full_opts_into_big_code(runner: Runner) -> None:
    argv = _confirmed_push(runner) + ["--qr", "full"]
    code, text = _color_run(runner, argv, 120)
    assert code == 0
    blank = [line for line in text.split("\n") if line.plain and set(line.plain) == {" "}]
    assert len(blank) >= 37 and "▀" not in text.plain  # 整格画法：每个模块行一行，全是带背景色的空格


# ---------------------------------------------------------------- 0.2.3：--qr fancy 麦当劳主题码

APP_URL = R.app_pay_url("https://m.mcd.cn/mcp/scanToPay?orderId=" + "1030" + "0" * 24)


def _bg_colors(text: Text) -> set:
    """收集二维码里所有出现过的背景色（小写十六进制，不带 #）。"""
    colors = set()
    for line in text.split("\n"):
        for col in range(len(line.plain)):
            style = line.get_style_at_offset(_CONSOLE, col)
            if style.bgcolor is not None:
                colors.add(_hex(style.bgcolor))
    return colors


def _decode_fancy_non_logo(text: Text) -> List[List[bool]]:
    """fancy 整格：按背景色解码（金=深、白=浅），正中 11x11 Logo 区按坐标置 None 跳过比对。"""
    lines = text.split("\n")
    n = len(lines)
    size = len(next(iter(R._FANCY_PATTERNS.values()))) + 2  # 9 + 外圈红边 = 11
    lo = (n - size) // 2
    rows: List[List[bool]] = []
    for y, line in enumerate(lines):
        assert set(line.plain) == {" "}  # 整格画法全是空格
        row: List[bool] = []
        for i, col in enumerate(range(0, len(line.plain), 2)):
            style = line.get_style_at_offset(_CONSOLE, col)
            assert line.get_style_at_offset(_CONSOLE, col + 1) == style  # 一个模块两格同色
            if lo <= y < lo + size and lo <= i < lo + size:
                row.append(None)  # type: ignore[arg-type]  # Logo 区按坐标跳过
                continue
            hexv = _hex(style.bgcolor)
            row.append({"ffbc0d": True, LIGHT: False}[hexv])
        rows.append(row)
    return rows


def _logo_region(text: Text) -> List[List[object]]:
    """fancy 整格里正中 11x11 Logo 区的背景色（小写十六进制），非 Logo 区置 None。"""
    lines = text.split("\n")
    n = len(lines)
    size = len(next(iter(R._FANCY_PATTERNS.values()))) + 2  # 11
    lo = (n - size) // 2
    out: List[List[object]] = []
    for y, line in enumerate(lines):
        row: List[object] = []
        for i, col in enumerate(range(0, len(line.plain), 2)):
            if lo <= y < lo + size and lo <= i < lo + size:
                style = line.get_style_at_offset(_CONSOLE, col)
                row.append(_hex(style.bgcolor))
            else:
                row.append(None)
        out.append(row)
    return out


def test_qr_matrix_high_error_correction() -> None:
    assert len(R.qr_matrix(APP_URL, 2, high=True)) == 45  # 纠错 H + 静区 2 → n=45
    assert len(R.qr_matrix(APP_URL, 2)) == 37  # 默认仍 M → n=37，high 开关不影响现有调用


def test_six_templates_are_nine_by_nine() -> None:
    # 6 个模板都在，且都是 9x9（照抄 templates-6-prototype.py）
    assert set(R.FANCY_TEMPLATES) == set(R._FANCY_PATTERNS) == {"burger", "fries", "nuggets", "cone", "pie", "arch"}
    assert set(R.FANCY_LABELS) == set(R.FANCY_TEMPLATES)
    for name, pat in R._FANCY_PATTERNS.items():
        assert len(pat) == 9 and all(len(row) == 9 for row in pat), name


def test_every_template_draws_full_cells_and_matches_high_matrix_outside_logo() -> None:
    expected = R.qr_matrix(APP_URL, 2, high=True)
    n = len(expected)
    size = len(next(iter(R._FANCY_PATTERNS.values()))) + 2  # 11
    lo = (n - size) // 2
    for name, pat in R._FANCY_PATTERNS.items():
        text = R.qr_text(APP_URL, color=True, width=120, fancy=True, template=name)
        lines = text.split("\n")
        assert len(lines) == n and all(cell_len(line.plain) == n * 2 for line in lines), name  # 整格
        assert all(set(line.plain) == {" "} for line in lines), name  # 无「▀」
        # 非 Logo 区逐格等于纠错 H 矩阵
        decoded = _decode_fancy_non_logo(text)
        for y in range(n):
            for x in range(n):
                if decoded[y][x] is not None:
                    assert decoded[y][x] == expected[y][x], (name, y, x)
        # 三色都落上了：金、白、红
        assert {"ffbc0d", "ffffff", "da291c"} <= _bg_colors(text), name
        # Logo 区：外圈 11x11 全红，内 9x9 按各自图案（1=金、0=红）
        logo = _logo_region(text)
        for r in range(size):
            for c in range(size):
                cell = logo[lo + r][lo + c]
                inner = 1 <= r <= 9 and 1 <= c <= 9
                want = "ffbc0d" if (inner and pat[r - 1][c - 1]) else "da291c"
                assert cell == want, (name, r, c)


def test_resolve_fancy_template_specific_random_and_invalid() -> None:
    # 指定生效
    assert R.resolve_fancy_template("fries") == "fries"
    # 非法名字报错并列出可选
    import pytest

    with pytest.raises(R.FancyTemplateError) as e1:
        R.resolve_fancy_template("bigmac")
    assert all(t in str(e1.value) for t in R.FANCY_TEMPLATES)
    # parse_qr_style 也要在解析期就挡住非法名字
    with pytest.raises(R.FancyTemplateError):
        R.parse_qr_style("fancy=bigmac")
    assert R.parse_qr_style("fancy=cone") == ("fancy", "cone")
    assert R.parse_qr_style("fancy:pie") == ("fancy", "pie")
    assert R.parse_qr_style("fancy") == ("fancy", None)
    assert R.parse_qr_style("full") == ("full", None)
    # env 固定（截图 / 复现用）
    assert R.resolve_fancy_template(None, env={R._FANCY_ENV: "nuggets"}) == "nuggets"


def test_random_is_seed_reproducible_and_covers_all_templates() -> None:
    import random as _random

    # 同一 seed 复现同一结果
    a = R.resolve_fancy_template(None, rng=_random.Random(7))
    b = R.resolve_fancy_template(None, rng=_random.Random(7))
    assert a == b and a in R.FANCY_TEMPLATES
    # 足够多步数能覆盖到全部 6 个模板
    rng = _random.Random(0)
    seen = {R.resolve_fancy_template(None, rng=rng) for _ in range(400)}
    assert seen == set(R.FANCY_TEMPLATES)


def test_fancy_falls_back_to_half_when_terminal_too_narrow() -> None:
    text = R.qr_text(APP_URL, color=True, width=80, fancy=True)  # width < 45*2=90 → 退回 half
    assert "▀" in text.plain
    assert "da291c" not in _bg_colors(text)  # 退回普通 half，没有麦当劳红


def test_fancy_without_color_is_plain() -> None:
    text = R.qr_text(APP_URL, color=False, width=120, fancy=True)  # 没颜色 fancy 不生效
    assert not text.spans


def test_pay_block_fancy_wide_terminal_shows_theme_hint_with_template_name() -> None:
    pay = "https://m.mcd.cn/mcp/scanToPay?orderId=" + "1030" * 7
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
    R.pay_block(console, pay, style="fancy=cone")  # 指定甜筒，复现稳定
    text = Text.from_ansi(buf.getvalue())
    assert "麦当劳主题码·甜筒，和普通码一样扫" in text.plain
    qr_lines = [line for line in text.split("\n") if line.plain and set(line.plain) == {" "}]
    assert _decode_fancy_non_logo(Text("\n").join(qr_lines))  # 画成了整格 fancy
    assert "支付链接：" + pay in text.plain  # 文字链接仍是原支付链接


def test_pay_block_fancy_env_pins_template() -> None:
    pay = "https://m.mcd.cn/mcp/scanToPay?orderId=" + "1030" * 7
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120, highlight=False)
    R.pay_block(console, pay, style="fancy", env={R._FANCY_ENV: "fries"})  # 随机但 env 固定
    text = Text.from_ansi(buf.getvalue())
    assert "麦当劳主题码·薯条，和普通码一样扫" in text.plain


def test_pay_block_fancy_narrow_falls_back_with_notice() -> None:
    pay = "https://m.mcd.cn/mcp/scanToPay?orderId=" + "1030" * 7
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=80, highlight=False)
    R.pay_block(console, pay, style="fancy")
    text = Text.from_ansi(buf.getvalue())
    assert "终端太窄，麦当劳主题码已退回普通二维码" in text.plain
    assert "▀" in text.plain


def test_push_qr_fancy(runner: Runner) -> None:
    argv = _confirmed_push(runner) + ["--qr", "fancy=burger"]
    code, text = _color_run(runner, argv, 120)
    assert code == 0
    assert "麦当劳主题码·汉堡，和普通码一样扫" in text.plain


def test_push_qr_fancy_invalid_template_errors(runner: Runner) -> None:
    argv = _confirmed_push(runner) + ["--qr", "fancy=bigmac"]
    code, text = _color_run(runner, argv, 120)
    assert code != 0
    assert "bigmac" in text.plain
    for name in R.FANCY_TEMPLATES:
        assert name in text.plain
