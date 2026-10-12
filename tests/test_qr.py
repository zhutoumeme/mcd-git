"""终端二维码：0.2.4 起默认整格（静区 2），放不下退回半块并提示；half 可选（静区 4）；无颜色 plain；fancy 主题码。
链接是合成的：和真实支付链接同形态（https://m.mcd.cn/mcp/scanToPay?orderId= + 28 位数字，67 字节）。
"""
from __future__ import annotations

import io
import os
from typing import List, Tuple

import pytest
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


# ---------------------------------------------------------------- 0.2.4 默认整格（静区 2）：各终端都扫得出


def test_full_is_the_default_and_draws_each_module_as_two_background_cells() -> None:
    assert R.QR_QUIET_FULL == 2
    assert R.qr_layout(URL, color=True, width=200) == ("full", 2, None)
    text = R.qr_text(URL, width=200)  # 不传 style
    lines = text.split("\n")
    assert len(lines) == 37 and all(cell_len(line.plain) == 74 for line in lines)
    assert _decode_full(text) == R.qr_matrix(URL, 2)
    assert _quiet(_decode_full(text), 2)


def test_full_exactly_fits_at_twice_the_side() -> None:
    assert R.qr_layout(URL, color=True, width=74) == ("full", 2, None)
    lines = R.qr_text(URL, color=True, width=74).split("\n")
    assert len(lines) == 37 and all(cell_len(line.plain) == 74 for line in lines)


def test_default_full_falls_back_to_half_when_terminal_too_narrow() -> None:
    assert R.qr_layout(URL, color=True, width=73) == ("half", 4, "full")  # 差一列就退回半块
    text = R.qr_text(URL, color=True, width=73)
    lines = text.split("\n")
    assert len(lines) == 21 and all(cell_len(line.plain) == 41 for line in lines)
    assert _decode_half(text)[:41] == R.qr_matrix(URL)
    assert R.qr_layout(URL, color=True, width=40) == ("half", 2, "full")  # 半块静区 4 也放不下 → 静区 2
    assert len(R.qr_text(URL, color=True, width=40).split("\n")) == 19


# ---------------------------------------------------------------- half 可选：更紧凑（静区 4，放不下退静区 2）


def test_half_is_opt_in_and_compact() -> None:
    assert R.qr_layout(URL, color=True, width=200, style="half") == ("half", 4, None)  # 显式 half 不算退回
    text = R.qr_text(URL, color=True, width=200, style="half")
    lines = text.split("\n")
    assert len(lines) == 21 and all(cell_len(line.plain) == 41 for line in lines)
    decoded = _decode_half(text)
    assert decoded[-1] == [False] * 41  # 最后一行的下半格补浅色
    assert decoded[:41] == R.qr_matrix(URL)


def test_half_mode_falls_back_to_quiet_two_when_even_half_does_not_fit() -> None:
    assert R.qr_layout(URL, color=True, width=40, style="half") == ("half", 2, None)
    text = R.qr_text(URL, color=True, width=40, style="half")
    lines = text.split("\n")
    assert len(lines) == 19 and all(cell_len(line.plain) == 37 for line in lines)
    assert _decode_half(text)[:37] == R.qr_matrix(URL, 2)


def test_plain_mode_keeps_qrencode_style_with_quiet_four() -> None:
    for style in R.QR_STYLES:  # 没颜色时不论哪种画法都是 plain
        assert R.qr_layout(URL, color=False, width=200, style=style) == ("plain", 4, None)
        text = R.qr_text(URL, color=False, width=200, style=style)
        assert not text.spans
        lines = text.plain.split("\n")
        assert len(lines) == 21 and all(len(line) == 41 for line in lines)
        assert _decode_plain(text)[:41] == R.qr_matrix(URL)


def test_qr_style_constants_and_defaults() -> None:
    from mcd_git.commands.common import validate_qr

    assert R.QR_STYLES == ("full", "half", "fancy")
    assert R.QR_STYLE_VALUES == ("full", "half", "fancy") + tuple("fancy=" + t for t in R.FANCY_TEMPLATES)
    assert R.parse_qr_style(None) == ("full", None)
    assert R.parse_qr_style("") == ("full", None)
    assert validate_qr(None) == "full"
    with pytest.raises(ValueError):
        R.qr_layout(URL, color=True, width=200, style="big")


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


PAY = "https://m.mcd.cn/mcp/scanToPay?orderId=" + "1030" * 7
FALLBACK_FULL = "放不下整格二维码"


def _pay_block(width: int, **kwargs: object) -> Text:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, color_system="truecolor", width=width, highlight=False)
    R.pay_block(console, PAY, **kwargs)  # type: ignore[arg-type]
    return Text.from_ansi(buf.getvalue())


def _flat(text: Text) -> str:
    return text.plain.replace("\n", "")  # 窄终端里提示会折行，按整段比对


def test_pay_block_default_full_encodes_app_link_and_keeps_pay_link_text() -> None:
    text = _pay_block(120)  # 默认 full
    qr_lines = [line for line in text.split("\n") if line.plain and set(line.plain) == {" "}]
    assert len(qr_lines) == 37 and "▀" not in text.plain  # 整格画法、静区 2
    assert _decode_full(Text("\n").join(qr_lines)) == R.qr_matrix(R.app_pay_url(PAY), 2)  # 二维码是 App 链接
    assert "支付链接：" + PAY in text.plain  # 文字链接仍是原支付链接（浏览器打开）
    assert "hint: 用手机扫上面的二维码，会直接打开麦当劳 App 付款（mcd-git 不会替你付款）" in text.plain
    assert "hint: 扫不出来：在浏览器里打开支付链接，或到麦当劳 App 订单页付款" in text.plain
    assert FALLBACK_FULL not in text.plain


def test_pay_block_narrow_falls_back_to_half_with_one_line_notice() -> None:
    text = _pay_block(60)
    qr_lines = [line for line in text.split("\n") if "▀" in line.plain]
    assert len(qr_lines) == 21  # 60 < 74 放不下整格 → half；60 ≥ 41 半块静区 4 放得下
    flat = _flat(text)
    notice = ("终端只有 60 列，放不下整格二维码（要 74 列），已退回更紧凑的半块画法；"
              "部分终端（如 macOS Terminal.app）半块可能扫不出，把窗口拉宽后再运行 git mcd show 重新出码")
    assert notice in flat
    assert "macOS Terminal.app" in flat and "git mcd show" in flat
    # 退回提示排在 tips 最前，原有两条提示逐字保留
    assert flat.index(notice) < flat.index("用手机扫上面的二维码")
    assert "用手机扫上面的二维码，会直接打开麦当劳 App 付款（mcd-git 不会替你付款）" in flat
    assert "扫不出来：在浏览器里打开支付链接，或到麦当劳 App 订单页付款" in flat


def test_pay_block_explicit_half_has_no_fallback_notice() -> None:
    for width in (120, 60):
        text = _pay_block(width, style="half")
        assert "▀" in text.plain
        assert FALLBACK_FULL not in _flat(text)


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


def test_push_defaults_to_full_qr(runner: Runner) -> None:
    # 默认（不带 --qr）用整格：不依赖「▀」字形，macOS Terminal.app 也扫得出
    argv = _confirmed_push(runner)
    code, text = _color_run(runner, argv, 120)
    assert code == 0 and "▀" not in text.plain
    blank = [line for line in text.split("\n") if line.plain and set(line.plain) == {" "}]
    assert len(blank) >= 37
    assert "扫不出来" in text.plain and FALLBACK_FULL not in text.plain


def test_push_qr_half_opts_into_compact_code(runner: Runner) -> None:
    argv = _confirmed_push(runner) + ["--qr", "half"]
    code, text = _color_run(runner, argv, 120)
    assert code == 0 and "▀" in text.plain
    assert FALLBACK_FULL not in _flat(text)


def test_push_narrow_terminal_falls_back_to_half_with_notice(runner: Runner) -> None:
    argv = _confirmed_push(runner)
    code, text = _color_run(runner, argv, 60)
    assert code == 0 and "▀" in text.plain
    assert FALLBACK_FULL in _flat(text) and "macOS Terminal.app" in _flat(text)


def test_show_pending_order_defaults_to_full_qr(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcd_git.demo.client import DemoClient

    def still_pending(self: DemoClient, args: dict) -> dict:  # demo 默认查单即「已付款」；这里让订单保持待支付
        db = self._load_db()
        return self._view(self._find(db, str(args.get("orderId") or "")))

    monkeypatch.setattr(DemoClient, "_t_query_order", still_pending)
    runner.run(*_confirmed_push(runner))
    code, text = _color_run(runner, ["--demo", "show"], 120)
    assert code == 0 and "待支付" in text.plain
    blank = [line for line in text.split("\n") if line.plain and set(line.plain) == {" "}]
    assert len(blank) >= 37 and "▀" not in text.plain


def test_push_qr_bogus_lists_choices(runner: Runner) -> None:
    code, data = runner.json(*_confirmed_push(runner), "--qr", "bogus")
    assert code != 0 and "bogus" in data["error"]["message"]
    assert data["error"]["hints"][0].startswith("可选：full / half / fancy / fancy=名字")


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
        text = R.qr_text(APP_URL, color=True, width=120, style="fancy", template=name)
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


def test_fancy_falls_back_to_full_first_then_half() -> None:
    # 80 < 45*2=90 放不下 fancy，但 ≥ 37*2=74 → 先退整格，不是 half
    assert R.qr_layout(APP_URL, color=True, width=80, style="fancy") == ("full", 2, "fancy")
    text = R.qr_text(APP_URL, color=True, width=80, style="fancy")
    assert "▀" not in text.plain and len(text.split("\n")) == 37
    assert _decode_full(text) == R.qr_matrix(APP_URL, 2)
    assert "da291c" not in _bg_colors(text)  # 退回普通码，没有麦当劳红
    # 60 连整格都放不下 → half
    assert R.qr_layout(APP_URL, color=True, width=60, style="fancy") == ("half", 4, "fancy+full")
    assert "▀" in R.qr_text(APP_URL, color=True, width=60, style="fancy").plain


def test_fancy_without_color_is_plain() -> None:
    text = R.qr_text(APP_URL, color=False, width=120, style="fancy")  # 没颜色 fancy 不生效
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


def test_pay_block_fancy_narrow_falls_back_to_full_with_notice() -> None:
    text = _pay_block(80, style="fancy")
    assert "终端太窄，麦当劳主题码已退回普通二维码" in _flat(text)
    assert "▀" not in text.plain  # 80 列放得下整格
    assert FALLBACK_FULL not in _flat(text)


def test_pay_block_fancy_very_narrow_falls_back_to_half_with_both_notices() -> None:
    text = _pay_block(60, style="fancy")
    flat = _flat(text)
    assert "▀" in text.plain
    assert "终端太窄，麦当劳主题码已退回普通二维码" in flat and FALLBACK_FULL in flat
    assert flat.index("终端太窄，麦当劳主题码") < flat.index(FALLBACK_FULL) < flat.index("用手机扫上面的二维码")


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
