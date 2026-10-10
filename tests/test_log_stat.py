"""git mcd log --stat：吃麦统计（全部离线）。口径：已取消、待支付的单照常显示但不计入金额和分布。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from mcd_git.demo import world
from mcd_git.errors import McdError
from mcd_git.ledger import Ledger

from .conftest import Runner, mcp_result
from .test_push_gate import PUSH, _setup

HISTORY_IDS = [h["orderId"] for h in world.HISTORY]

# 手工账本：4 单吃了（早餐 / 午饭 / 下午茶 / 夜宵各一）+ 1 单取消 + 1 单待支付
ENTRIES: List[Dict[str, Any]] = [
    {"key": "a" * 40, "time": "2026-10-09 22:30:00", "state": "completed", "status": "订单已完成",
     "items": [{"name": "麦辣鸡腿堡套餐", "qty": 1}], "original": 4000, "discount": 0, "paid": 4000, "way": "麦乐送"},
    {"key": "b" * 40, "time": "2026-10-10 12:00:00", "state": "completed", "status": "订单已完成",
     "items": [{"name": "巨无霸套餐", "qty": 1}, {"name": "菠萝派", "qty": 2}], "original": 4500, "discount": 500, "paid": 4000, "way": "到店自取"},
    {"key": "c" * 40, "time": "2026-10-11 08:00:00", "state": "completed", "status": "订单已完成",
     "items": [{"name": "猪柳蛋麦满分", "qty": 1}, {"name": "热拿铁", "qty": 1}], "original": 2900, "discount": 0, "paid": 2900, "way": "到店自取"},
    {"key": "d" * 40, "time": "2026-10-12 15:30:00", "state": "completed", "status": "订单已完成",
     "items": [{"name": "菠萝派", "qty": 1}], "original": None, "discount": None, "paid": 800, "way": "到店自取"},
    {"key": "e" * 40, "time": "2026-10-13 12:10:00", "state": "cancelled", "status": "订单已取消",
     "items": [{"name": "双层吉士汉堡套餐", "qty": 3}], "original": 9600, "discount": 0, "paid": 9600, "way": "到店自取"},
    {"key": "f" * 40, "time": "2026-10-14 12:20:00", "state": "pending", "status": "待支付",
     "items": [{"name": "巨无霸套餐", "qty": 5}], "original": 19250, "discount": 0, "paid": 19250, "way": "到店自取"},
]


def _home(runner: Runner) -> Path:
    return Path(runner.tmp_path) / "mcd-git"


def _handmade(runner: Runner) -> None:
    """真实模式的账本里只放上面 6 单；order-list 返回空，不往里合并别的。"""
    Ledger(_home(runner), demo=False).upsert(ENTRIES)
    runner.hooks["order-list"] = lambda args, allow_write: mcp_result({"success": True, "code": 200, "data": {"list": []}})


def test_demo_log_stat_lists_items_and_summary(runner: Runner) -> None:
    code, out = runner.run("--demo", "log", "--stat", "-n", "3")
    assert code == 0
    assert " 巨无霸套餐 | 1 +" in out and " 1 样，实付 ¥33.50（省 ¥5.00）" in out
    assert " 猪柳蛋麦满分 | 1 +" in out and " 2 样，实付 ¥29.00" in out
    assert out.count("commit ") == 3
    assert " 3 单，4 样，实付 ¥102.50，省 ¥5.00" in out
    assert "按时段：" in out and "夜宵" in out and "最常点：" in out
    code, out = runner.run("--demo", "log", "--stat", "--since", "2026-10-20", "--until", "2026-10-20")
    assert code == 0 and "双层吉士汉堡套餐 | 1 +" in out and "1 样，已取消，不计入金额" in out
    assert " 1 单（其中 1 单已取消），1 样，实付 ¥0.00，省 ¥0.00" in out


def test_summary_excludes_cancelled_and_pending(runner: Runner) -> None:
    _handmade(runner)
    code, data = runner.json("log", "--stat", mock=True)
    assert code == 0
    s = data["data"]["summary"]
    assert (s["orders"], s["cancelled"], s["pending"], s["lines"]) == (6, 1, 1, 8)
    assert (s["paid"], s["saved"], s["savedUnknown"]) == (4000 + 4000 + 2900 + 800, 500, 1)  # 取消的 96 元、待支付的 192.5 元都不算
    # 时段只数吃了的单：待支付那单午饭、取消那单午饭都不算
    assert s["slots"] == {"breakfast": 1, "lunch": 1, "tea": 1, "dinner": 0, "night": 1}
    assert list(s["slots"]) == ["breakfast", "lunch", "tea", "dinner", "night"]
    # 按份数排，一样多按名字；待支付的 5 份巨无霸、取消的 3 份双层吉士都不算
    assert s["top"] == [{"name": "菠萝派", "count": 3}, {"name": "巨无霸套餐", "count": 1}, {"name": "热拿铁", "count": 1}]
    counted = {e["hash"][0]: e["counted"] for e in data["data"]["entries"]}
    assert counted == {"a": True, "b": True, "c": True, "d": True, "e": False, "f": False}

    code, out = runner.run("log", "--stat", mock=True)
    assert code == 0
    assert " 6 单（其中 1 单已取消，1 单待支付），8 样，实付 ¥117.00，省 ¥5.00" in out
    assert " 2 样，实付 ¥40.00（省 ¥5.00）" in out and " 菠萝派     | 2 ++" in out
    assert " 1 样，已取消，不计入金额" in out and " 1 样，待支付 ¥192.50，不计入金额" in out
    assert "其中 1 单没有优惠明细" in out and "git mcd show ddddddd" in out
    slots = re.findall(r"^  (早餐|午饭|下午茶|晚饭|夜宵)\s+\| (\d+)( \+*)?$", out, re.M)
    assert [(label, int(n)) for label, n, _ in slots] == [("早餐", 1), ("午饭", 1), ("下午茶", 1), ("晚饭", 0), ("夜宵", 1)]
    assert all(len(bar.strip()) == 20 for _, n, bar in slots if n != "0")  # 一样多都画满 20 个 +
    top = re.findall(r"^ +(\d+)  (\S+)$", out.split("最常点：")[1], re.M)
    assert top == [("3", "菠萝派"), ("1", "巨无霸套餐"), ("1", "热拿铁")]


def test_since_until_and_max_count(runner: Runner) -> None:
    _handmade(runner)
    code, data = runner.json("log", "--stat", "-n", "2", mock=True)
    assert code == 0 and [e["hash"][0] for e in data["data"]["entries"]] == ["f", "e"]  # 最新的 2 单
    assert data["data"]["summary"]["orders"] == 2 and data["data"]["range"]["maxCount"] == 2
    code, data = runner.json("log", "--stat", "--since", "2026-10-11", "--until", "2026-10-13", mock=True)
    assert [e["hash"][0] for e in data["data"]["entries"]] == ["e", "d", "c"]  # 两头都含当天
    assert data["data"]["range"] == {"since": "2026-10-11", "until": "2026-10-13", "maxCount": 10}
    assert data["data"]["summary"]["paid"] == 2900 + 800
    code, data = runner.json("log", "--stat", "--since", "2026-10-10", "-n", "2", mock=True)
    assert [e["hash"][0] for e in data["data"]["entries"]] == ["f", "e"]  # 先过滤日期，再取最新 n 单
    code, data = runner.json("log", "--stat", "--until", "2026-10-10", mock=True)
    assert [e["hash"][0] for e in data["data"]["entries"]] == ["b", "a"]
    code, out = runner.run("log", "--stat", "--since", "2026-11-01", mock=True)
    assert code == 0 and "这段时间里没有吃麦记录" in out


def test_bad_arguments(runner: Runner) -> None:
    code, out = runner.run("--demo", "log", "--stat", "--since", "2026-13-01")
    assert code == 1 and "--since 2026-13-01 看不懂" in out and "写成 2026-10-01" in out
    code, out = runner.run("--demo", "log", "--stat", "--until", "10-24")
    assert code == 1 and "--until 10-24 看不懂" in out
    for flag in ("--graph", "--oneline"):
        code, out = runner.run("--demo", "log", "--stat", flag)
        assert code == 1 and "--stat 不能和 --graph / --oneline 一起用" in out
    code, out = runner.run("--demo", "log", "--since", "2026-10-01")
    assert code == 1 and "只和 --stat 一起用" in out


def test_refreshes_order_list_once_and_survives_failure(runner: Runner) -> None:
    code, data = runner.json("log", "--stat", mock=True)
    assert code == 0 and runner.count("order-list") == 1
    assert data["mcpCalls"] == {"order-list": 1}
    assert data["data"]["summary"]["orders"] == 5 and data["data"]["summary"]["cancelled"] == 1  # 演示历史 5 单记进了账本

    def down(args: Dict[str, Any], allow_write: bool) -> Dict[str, Any]:
        raise McdError("网络错误：timed out", transport=True)

    runner.hooks["order-list"] = down
    code, data = runner.json("log", "--stat", mock=True)
    assert code == 0 and data["data"]["warning"].startswith("order-list 查询失败")
    assert data["data"]["summary"]["orders"] == 5  # 退回只用本机账本
    code, out = runner.run("log", "--stat", mock=True)
    assert code == 0 and "warning: order-list 查询失败" in out


def test_empty_and_broken_ledger(runner: Runner) -> None:
    runner.hooks["order-list"] = lambda args, allow_write: mcp_result({"success": True, "code": 200, "data": {"list": []}})
    code, data = runner.json("log", "--stat", mock=True)
    assert code == 0 and data["data"]["entries"] == [] and data["data"]["summary"]["orders"] == 0
    code, out = runner.run("log", "--stat", mock=True)
    assert code == 0 and "还没有吃麦记录" in out
    path = _home(runner) / "ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    code, out = runner.run("log", "--stat", mock=True)
    assert code == 128 and "损坏了" in out and "删掉它" in out


def test_hash_matches_log_and_push(runner: Runner) -> None:
    code, plain = runner.json("log", "--oneline", mock=True)
    code, stat = runner.json("log", "--stat", mock=True)
    first = next(e for e in plain["data"]["entries"] if e["orderId"] == HISTORY_IDS[0])
    assert stat["data"]["entries"][0]["hash"] == first["hash"]
    _setup(runner)
    code, data = runner.json(*PUSH, mock=True)
    head = data["data"]["summary"]["commit"]
    code, data = runner.json(*PUSH, "--confirm", data["data"]["confirm"], mock=True)
    assert code == 0 and data["data"]["status"] == "pushed"
    code, stat = runner.json("log", "--stat", "-n", "1", mock=True)
    entry = stat["data"]["entries"][0]
    assert entry["hash"] == head and entry["state"] == "pending" and entry["counted"] is False
    code, out = runner.run("log", "--stat", "-n", "1", mock=True)
    assert f"commit {head}" in out and "1 样，待支付 ¥33.50，不计入金额" in out and "1 单待支付" in out


def test_no_order_ids_stores_or_calories(runner: Runner) -> None:
    code, out = runner.run("log", "--stat", mock=True)
    assert code == 0
    code, data = runner.json("log", "--stat", mock=True)
    text = json.dumps(data, ensure_ascii=False)
    for blob in (out, text):
        assert not any(oid in blob for oid in HISTORY_IDS) and not re.search(r"\d{28}", blob)
        assert "演示·" not in blob and "示例市" not in blob
        assert "热量" not in blob and "kcal" not in blob.lower() and "卡路里" not in blob
