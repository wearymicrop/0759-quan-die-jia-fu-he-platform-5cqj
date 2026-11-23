import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import db_path
from app.modules.budget import ledger


def sale(rid, at, paid, coupons, price="25.00", category="茶饮", shop="滨河店", qty=1):
    return json.dumps({"type": "sale", "id": rid, "at": at, "shop": shop, "paid": paid,
                       "coupons": coupons,
                       "lines": [{"id": "01", "category": category, "qty": qty, "price": price}]},
                      ensure_ascii=False)


RULES = {"versions": [
    {"code": "品类九折", "version": "一版", "group": "品类", "kind": "percent", "value": 1000,
     "categories": ["茶饮"], "start": "2026-11-21T00:00:00+08:00", "end": "2026-11-24T00:00:00+08:00", "rank": 10},
    {"code": "店券5", "version": "一版", "group": "店券", "kind": "fixed", "value": "5.00",
     "minimum": "20.00", "start": "2026-11-21T00:00:00+08:00", "end": "2026-11-24T00:00:00+08:00", "rank": 20},
    {"code": "店券2", "version": "一版", "group": "店券", "kind": "fixed", "value": "2.00",
     "start": "2026-11-21T00:00:00+08:00", "end": "2026-11-24T00:00:00+08:00", "rank": 20}],
    "pairs": [["品类九折", "店券5"], ["品类九折", "店券2"]]}

DAY = "2026-11-22"
POOL = [{"shop": "滨河店", "day": DAY, "amount": "12.00", "groups": ["店券"]}]


def contention():
    events = "\n".join([
        sale("A1", f"{DAY}T09:00:00+08:00", "20.00", ["店券5"]),
        sale("A2", f"{DAY}T09:01:00+08:00", "20.00", ["店券5"]),
        sale("A3", f"{DAY}T09:02:00+08:00", "20.00", ["店券5"]),
        sale("A4", f"{DAY}T09:03:00+08:00", "8.00", ["店券2"], price="10.00", category="杂货")])
    return {"events": events, "rules": RULES, "budgets": POOL}


def post(client, payload):
    response = client.post("/api/budgets/batches", json=payload)
    assert response.status_code == 200, response.json()
    return response.json()


def receipt(record, rid):
    return next(row for row in record["receipts"] if row["id"] == rid)


def test_first_come_keeps_discount_and_latecomer_fits(client):
    record = post(client, contention())
    assert receipt(record, "A1")["occupied"] == 500
    assert receipt(record, "A2")["occupied"] == 500
    late = receipt(record, "A3")
    assert (late["status"], late["reason"], late["dropped"]) == ("unfunded", "BUDGET_EXHAUSTED", ["店券5"])
    assert (late["combo"], late["due"], late["paid"], late["gap"]) == ([], 2500, 2000, -500)
    fit = receipt(record, "A4")
    assert (fit["status"], fit["occupied"], fit["due"]) == ("funded", 200, 800)
    pool = next(row for row in record["pools"] if row["shop"] == "滨河店")
    assert (pool["used"], pool["remaining"], pool["batch_held"]) == (1200, 0, 1200)


def test_gates_block_formal_until_three_way_matched(client):
    record = post(client, contention())
    assert record["status"] == "pending"
    gates = {gate["check"]: gate for gate in record["gates"]}
    assert gates["COMBO_BALANCE"]["status"] == "PASS"
    assert gates["POOL_BALANCE"]["status"] == "PASS"
    assert gates["PAID_MATCH"]["status"] == "FAIL"
    assert gates["PAID_MATCH"]["findings"] == [{"id": "A3", "reason": "PAID_MISMATCH", "gap": -500}]


def test_formal_when_combo_pool_and_paid_align(client):
    events = "\n".join([
        sale("A1", f"{DAY}T09:00:00+08:00", "20.00", ["店券5"]),
        sale("A3", f"{DAY}T09:02:00+08:00", "25.00", ["店券5"])])
    tight = [{"shop": "滨河店", "day": DAY, "amount": "5.00", "groups": ["店券"]}]
    record = post(client, {"events": events, "rules": RULES, "budgets": tight})
    assert record["status"] == "formal"
    assert receipt(record, "A3")["reason"] == "BUDGET_EXHAUSTED"
    assert all(gate["status"] == "PASS" for gate in record["gates"])


def test_redelivery_neither_double_occupies_nor_loses_record(client):
    first = post(client, contention())
    again = post(client, contention())
    assert again["reused"] is True and again["id"] == first["id"]
    fetched = client.get(f"/api/budgets/batches/{first['id']}").json()
    assert fetched["receipts"] == first["receipts"]
    pool = next(row for row in fetched["pools"] if row["shop"] == "滨河店")
    assert pool["used"] == 1200
    with sqlite3.connect(db_path()) as conn:
        held = conn.execute("SELECT COUNT(*) FROM budget_allocations WHERE status='HELD'").fetchone()[0]
    assert held == 3


def test_cross_batch_duplicate_not_double_occupied(client):
    post(client, contention())
    events = "\n".join([
        sale("A1", f"{DAY}T09:00:00+08:00", "20.00", ["店券5"]),
        sale("B1", f"{DAY}T10:00:00+08:00", "25.00", ["店券5"])])
    second = post(client, {"events": events, "rules": RULES, "budgets": POOL})
    duplicate = receipt(second, "A1")
    assert (duplicate["status"], duplicate["reason"], duplicate["occupied"]) == \
        ("duplicate", "DUPLICATE_DELIVERY", 0)
    assert duplicate["original_batch"] != second["id"]
    pool = next(row for row in second["pools"] if row["shop"] == "滨河店")
    assert pool["used"] == 1200


def test_void_releases_quota_and_is_idempotent(client):
    record = post(client, contention())
    voided = client.post(f"/api/budgets/batches/{record['id']}/void", json={"receipts": ["A1"]}).json()
    assert voided["void"] == {"released": [{"id": "A1", "amount": 500}], "already_void": [], "unknown": []}
    pool = next(row for row in voided["pools"] if row["shop"] == "滨河店")
    assert pool["used"] == 700
    row = receipt(voided, "A1")
    assert (row["voided"], row["occupied"], row["released"]) == (True, 0, 500)
    again = client.post(f"/api/budgets/batches/{record['id']}/void",
                        json={"receipts": ["A1", "ZZ"]}).json()
    assert again["void"]["already_void"] == ["A1"] and again["void"]["unknown"] == ["ZZ"]
    pool = next(row for row in again["pools"] if row["shop"] == "滨河店")
    assert pool["used"] == 700


def test_void_clears_paid_gate_and_freed_quota_serves_retry(client):
    record = post(client, contention())
    voided = client.post(f"/api/budgets/batches/{record['id']}/void",
                         json={"receipts": ["A1", "A3"]}).json()
    assert voided["status"] == "formal"
    events = sale("A3", f"{DAY}T09:02:00+08:00", "20.00", ["店券5"])
    retry = post(client, {"events": events, "rules": RULES, "budgets": POOL})
    row = receipt(retry, "A3")
    assert (row["status"], row["occupied"], row["due"]) == ("funded", 500, 2000)
    assert retry["status"] == "formal"


def test_partial_downgrade_keeps_fitting_coupon(client):
    rules = {"versions": RULES["versions"] + [
        {"code": "惠民券3", "version": "一版", "group": "惠民", "kind": "fixed", "value": "3.00",
         "start": "2026-11-21T00:00:00+08:00", "end": "2026-11-24T00:00:00+08:00", "rank": 30}],
        "pairs": [["店券5", "惠民券3"]]}
    events = sale("A1", f"{DAY}T09:00:00+08:00", "25.00", ["店券5", "惠民券3"], price="30.00")
    pools = [{"shop": "滨河店", "day": DAY, "amount": "5.00", "groups": ["店券", "惠民"]}]
    record = post(client, {"events": events, "rules": rules, "budgets": pools})
    row = receipt(record, "A1")
    assert (row["status"], row["combo"], row["occupied"], row["due"]) == ("funded", ["店券5"], 500, 2500)
    assert (row["reason"], row["dropped"], row["best_combo"], row["best_due"]) == \
        ("BUDGET_EXHAUSTED", ["惠民券3"], ["店券5", "惠民券3"], 2200)
    assert record["status"] == "formal"


def test_missing_pool_denies_store_coupon(client):
    events = sale("A1", f"{DAY}T09:00:00+08:00", "20.00", ["店券5"])
    record = post(client, {"events": events, "rules": RULES,
                           "budgets": [{"shop": "北门店", "day": DAY, "amount": "5.00"}]})
    row = receipt(record, "A1")
    assert (row["status"], row["reason"], row["occupied"]) == ("unfunded", "POOL_MISSING", 0)


def test_parallel_posts_occupy_once(client):
    with ThreadPoolExecutor(4) as pool:
        records = list(pool.map(lambda _: post(client, contention()), range(4)))
    assert len({record["id"] for record in records}) == 1
    assert [record["reused"] for record in records].count(False) == 1
    with sqlite3.connect(db_path()) as conn:
        used = conn.execute("SELECT used FROM budget_pools WHERE shop='滨河店'").fetchone()[0]
        held = conn.execute("SELECT COUNT(*) FROM budget_allocations WHERE status='HELD'").fetchone()[0]
    assert (used, held) == (1200, 3)


def test_interrupted_commit_leaves_no_trace(client, monkeypatch):
    client.get("/api/budgets/pools")
    def crash(*args):
        raise RuntimeError("模拟中断")
    monkeypatch.setattr(ledger, "persist_record", crash)
    with pytest.raises(RuntimeError):
        client.post("/api/budgets/batches", json=contention(), )
    with sqlite3.connect(db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_pools").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM budget_allocations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM budget_batches").fetchone()[0] == 0
    monkeypatch.undo()
    assert post(client, contention())["pools"][0]["used"] == 1200


def test_invalid_budget_is_atomic(client):
    client.get("/api/budgets/pools")
    response = client.post("/api/budgets/batches",
                           json={"events": sale("A1", f"{DAY}T09:00:00+08:00", "20.00", ["店券5"]),
                                 "rules": RULES, "budgets": [{"shop": "滨河店", "day": DAY, "amount": "很多"}]})
    assert response.status_code == 422 and response.json()["code"] == "BAD_BUDGET"
    with sqlite3.connect(db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_batches").fetchone()[0] == 0


def test_budget_page_and_sample_inputs(client):
    assert client.get("/budget").status_code == 200
    assert client.get("/static/budget.js").status_code == 200
    sample = client.get("/api/budgets/inputs/budget").json()
    record = post(client, {"events": sample["events"], "rules": sample["rules"], "budgets": sample["budgets"]})
    b103 = receipt(record, "B103")
    assert (b103["reason"], b103["gap"]) == ("BUDGET_EXHAUSTED", -500)
    assert record["status"] == "pending"
    pools = client.get("/api/budgets/pools").json()["pools"]
    assert {row["shop"] for row in pools} == {"滨河店", "北门店"}
