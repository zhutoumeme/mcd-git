"""本机订单账本：合并规则、字段白名单、文件权限，以及 log / show / cherry-pick / push / revert 顺手记账（全部离线）。"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict

import pytest

from mcd_git import ledger
from mcd_git.demo import world
from mcd_git.errors import GitMcdError
from mcd_git.history import order_hash
from mcd_git.ledger import FIELDS, Ledger, from_history, merge

from .conftest import Runner
from .test_push_gate import PUSH, _setup

HISTORY_IDS = [h["orderId"] for h in world.HISTORY]


def _home(runner: Runner) -> Path:
    return Path(runner.tmp_path) / "mcd-git"


def _orders(path: Path) -> Dict[str, Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["orders"]


def test_merge_newer_state_wins_but_keeps_known_money() -> None:
    old = merge(None, {"time": "2026-10-24 11:50:00", "state": "pending", "status": "待支付", "items": [{"name": "巨无霸套餐", "qty": 1}],
                       "original": 3850, "discount": 500, "paid": 3350, "way": "到店自取 · 外带 · 店外取餐柜", "commit": "a" * 40, "source": "push"})
    new = merge(old, {"state": "completed", "status": "订单已完成", "paid": 3300, "original": None, "discount": None, "items": [],
                      "way": "到店自取", "commit": "b" * 40, "source": "order-list"})
    assert (new["state"], new["status"], new["paid"]) == ("completed", "订单已完成", 3300)  # 最新状态覆盖旧状态
    assert (new["original"], new["discount"]) == (3850, 500)  # order-list 不带原价 / 优惠，不会被冲成 null
    assert new["items"] == [{"name": "巨无霸套餐", "qty": 1}]  # 空列表不覆盖
    assert (new["commit"], new["source"]) == ("a" * 40, "push")  # 只写一次
    assert new["way"] == "到店自取 · 外带 · 店外取餐柜"  # 笼统的不盖掉具体的
    assert merge(new, {"way": "麦乐送"})["way"] == "麦乐送"
    assert set(new) == set(FIELDS)
    assert merge(None, {"discount": 0})["discount"] == 0  # 0 元优惠是已知值，不是「没有」


def test_upsert_without_create_never_makes_new_entries(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "home", demo=False)
    assert book.upsert([{"key": "k1", "state": "cancelled"}], create=False) == 0
    assert not book.path.exists()
    assert book.upsert([{"key": "k1", "state": "pending", "paid": 100}]) == 1
    assert book.upsert([{"key": "k1", "state": "pending", "paid": 100}]) == 0  # 没变就不重写
    assert book.upsert([{"key": "k1", "state": "cancelled"}, {"key": "k2", "state": "cancelled"}], create=False) == 1
    assert [(e["key"], e["state"], e["paid"]) for e in book.entries()] == [("k1", "cancelled", 100)]


def test_from_history_only_keeps_whitelisted_fields() -> None:
    raw = {"orderId": "1024202610231231000000000004", "storeCode": "1000001", "storeName": "演示·代码港店", "createTime": "2026-10-23 12:31:00",
           "orderType": 1, "beType": 1, "statusLabel": "订单已完成", "state": "completed", "total": 3350,
           "products": [{"code": "9900001001", "name": "巨无霸套餐", "qty": 1, "items": [{"name": "巨无霸"}]}]}
    entry = from_history(raw)
    assert set(entry) - {"key"} <= set(FIELDS)
    assert entry["key"] == order_hash(raw["orderId"])
    assert entry["items"] == [{"name": "巨无霸套餐", "qty": 1}]
    assert (entry["paid"], entry["original"], entry["discount"]) == (3350, None, None)
    text = json.dumps(entry, ensure_ascii=False)
    assert "演示·" not in text and "1000001" not in text and raw["orderId"] not in text
    assert entry["way"] == "到店自取"
    assert from_history(dict(raw, orderType=1, beType=5))["way"] == "得来速"
    assert from_history(dict(raw, orderType=2, beType=2))["way"] == "麦乐送"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX 权限位")
def test_ledger_files_are_private_and_demo_is_separate(runner: Runner) -> None:
    assert runner.run("log", mock=True)[0] == 0
    assert runner.run("--demo", "log")[0] == 0
    real, demo = _home(runner) / "ledger.json", _home(runner) / "demo" / "ledger.json"
    for path in (real, demo):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert real != demo and set(_orders(real)) <= set(_orders(demo))


def test_log_show_and_cherry_pick_merge_order_list(runner: Runner) -> None:
    assert runner.run("log", mock=True)[0] == 0
    path = _home(runner) / "ledger.json"
    orders = _orders(path)
    assert set(orders) == {order_hash(oid) for oid in HISTORY_IDS}
    entry = orders[order_hash(HISTORY_IDS[0])]
    assert entry["source"] == "order-list" and entry["state"] == "completed" and entry["paid"] == 3350
    assert entry["items"] == [{"name": "巨无霸套餐", "qty": 1}] and entry["time"] == "2026-10-23 12:31:00"
    assert orders[order_hash(HISTORY_IDS[-1])]["state"] == "cancelled"

    code, _ = runner.run("show", HISTORY_IDS[0], mock=True)
    assert code == 0
    entry = _orders(path)[order_hash(HISTORY_IDS[0])]
    assert (entry["original"], entry["discount"], entry["paid"]) == (3850, 500, 3350)  # show 顺手补上优惠
    assert entry["source"] == "order-list" and entry["way"] == "到店自取 · 堂食"

    assert runner.run("init", "--pick", "1", mock=True)[0] == 0
    before = runner.count("order-list")
    assert runner.run("cherry-pick", HISTORY_IDS[1], mock=True)[0] == 0
    assert runner.count("order-list") == before + 1

    text = path.read_text(encoding="utf-8")
    assert not any(oid in text for oid in HISTORY_IDS) and not re.search(r"\d{28}", text)  # 订单号原文不进账本
    assert "演示·" not in text and "示例市" not in text and "example.com" not in text and "138****" not in text
    assert not any(h["pickupCode"] in text for h in world.HISTORY if h["pickupCode"])


def test_push_links_commit_and_revert_marks_cancelled(runner: Runner) -> None:
    _setup(runner)
    code, data = runner.json(*PUSH, mock=True)
    head = data["data"]["summary"]["commit"]
    code, data = runner.json(*PUSH, "--confirm", data["data"]["confirm"], mock=True)
    assert code == 0 and data["data"]["status"] == "pushed"
    order_id = data["data"]["orderId"]
    path = _home(runner) / "ledger.json"
    entry = _orders(path)[ledger.key_of(order_id)]
    assert entry["commit"] == head and entry["source"] == "push"
    # 确认时 calculate-price 的套餐原价 38.50 − 应付 33.50（45.50 是 squash 之前单点的价，不在这里）
    assert (entry["original"], entry["discount"], entry["paid"]) == (3850, 500, 3350)
    assert entry["state"] == "pending" and entry["status"] == "待支付"
    assert entry["items"] == [{"name": "巨无霸套餐", "qty": 1}]
    assert entry["way"] == "到店自取 · 外带 · 店外取餐柜" and entry["time"] == "2026-10-24 11:50:00"
    text = path.read_text(encoding="utf-8")
    assert order_id not in text and "pay?orderId" not in text and "payH5Url" not in text

    code, data = runner.json("revert", "--reason", "1", mock=True)
    assert data["data"]["status"] == "confirmation_required"
    code, data = runner.json("revert", "--reason", "1", "--confirm", order_id, mock=True)
    assert code == 0 and data["data"]["status"] == "reverted"
    entry = _orders(path)[ledger.key_of(order_id)]
    assert entry["state"] == "cancelled" and entry["commit"] == head

    # 之后 log 再合并 order-list：状态跟着服务端走，commit 和更具体的取餐方式都留着
    assert runner.run("log", mock=True)[0] == 0
    entry = _orders(path)[ledger.key_of(order_id)]
    assert (entry["state"], entry["status"], entry["commit"], entry["source"]) == ("cancelled", "订单已取消", head, "push")
    assert entry["way"] == "到店自取 · 外带 · 店外取餐柜"


def test_ledger_write_failure_never_breaks_push(runner: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    def full(self: Ledger, orders: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Ledger, "save", full)
    _setup(runner)
    code, data = runner.json(*PUSH, mock=True)
    code, data = runner.json(*PUSH, "--confirm", data["data"]["confirm"], mock=True)
    assert code == 0 and data["ok"] is True and data["data"]["status"] == "pushed"
    assert runner.count("create-order") == 1
    assert not (_home(runner) / "ledger.json").exists()


def test_broken_ledger_never_breaks_read_commands(runner: Runner) -> None:
    path = _home(runner) / "ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    code, out = runner.run("log", "--oneline", mock=True)
    assert code == 0 and "巨无霸套餐" in out and "ledger" not in out
    code, _ = runner.run("show", HISTORY_IDS[0], mock=True)
    assert code == 0
    assert runner.run("init", "--pick", "1", mock=True)[0] == 0
    code, _ = runner.run("cherry-pick", HISTORY_IDS[1], mock=True)
    assert code == 0
    assert path.read_text(encoding="utf-8") == "{not json"  # 坏文件原样留着，不会被悄悄覆盖
    with pytest.raises(GitMcdError, match="损坏了"):
        Ledger(path.parent, demo=False).entries()
