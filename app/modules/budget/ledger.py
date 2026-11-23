import json
import sqlite3
from contextlib import closing, contextmanager
from time import perf_counter

from app.config import RULE_VERSION, db_path
from app.errors import DomainError
from app.manifest import canonical, sha
from app.modules.budget.adjudicate import adjudicate, evaluate
from app.modules.budget.pools import declared
from app.pipeline import process

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS budget_pools (
        shop TEXT NOT NULL, day TEXT NOT NULL, amount INTEGER NOT NULL,
        used INTEGER NOT NULL DEFAULT 0, groups TEXT NOT NULL,
        PRIMARY KEY (shop, day))""",
    """CREATE TABLE IF NOT EXISTS budget_batches (
        id TEXT PRIMARY KEY, source TEXT NOT NULL, record TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS budget_allocations (
        batch_id TEXT NOT NULL, receipt TEXT NOT NULL, shop TEXT NOT NULL,
        day TEXT NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL,
        PRIMARY KEY (batch_id, receipt))""",
    """CREATE UNIQUE INDEX IF NOT EXISTS budget_hold
        ON budget_allocations(shop, receipt) WHERE status = 'HELD'""",
)


@contextmanager
def transaction():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in SCHEMA:
                conn.execute(statement)
            yield conn
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def digest(source):
    files = {name: sha(canonical(source[name])) for name in ("events", "rules", "budgets")}
    return {"rule_version": RULE_VERSION, "files": files,
            "digest": sha(canonical([RULE_VERSION, files]))}


def pools_view(conn, batch_id):
    rows = conn.execute("""SELECT p.shop, p.day, p.amount, p.used, p.groups,
            COALESCE(SUM(CASE WHEN a.status = 'HELD' AND a.batch_id = ? THEN a.amount END), 0)
            FROM budget_pools p LEFT JOIN budget_allocations a ON a.shop = p.shop AND a.day = p.day
            GROUP BY p.shop, p.day ORDER BY p.shop, p.day""", (batch_id,)).fetchall()
    return [{"shop": shop, "day": day, "amount": amount, "used": used,
             "remaining": amount - used, "groups": json.loads(groups), "batch_held": held}
            for shop, day, amount, used, groups, held in rows]


def pool_findings(conn):
    findings = []
    rows = conn.execute("""SELECT p.shop, p.day, p.amount, p.used,
            COALESCE(SUM(CASE WHEN a.status = 'HELD' THEN a.amount END), 0)
            FROM budget_pools p LEFT JOIN budget_allocations a ON a.shop = p.shop AND a.day = p.day
            GROUP BY p.shop, p.day""").fetchall()
    for shop, day, amount, used, held in rows:
        if held != used or used > amount:
            findings.append({"shop": shop, "day": day, "reason": "POOL_IMBALANCE",
                             "amount": amount, "used": used, "held": held})
    return findings


def persist_record(conn, batch_id, source, record):
    conn.execute("INSERT INTO budget_batches VALUES (?, ?, ?)",
                 (batch_id, canonical(source), canonical(record)))


def load_record(conn, batch_id):
    row = conn.execute("SELECT record FROM budget_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise DomainError("BATCH_NOT_FOUND", "找不到该预算批次", 404)
    return json.loads(row[0])


def commit(source, label):
    started = perf_counter()
    book = declared(source["budgets"])
    result = process(source)
    meta = digest(source)
    with transaction() as conn:
        try:
            return {**load_record(conn, meta["digest"]), "reused": True}
        except DomainError as exc:
            if exc.code != "BATCH_NOT_FOUND":
                raise
        for (shop, day), pool in sorted(book.items()):
            conn.execute("INSERT INTO budget_pools VALUES (?, ?, ?, 0, ?) ON CONFLICT(shop, day) DO NOTHING",
                         (shop, day, pool["amount"], canonical(pool["groups"])))
        pools = {(shop, day): {"amount": amount, "used": used, "groups": json.loads(groups)}
                 for shop, day, amount, used, groups in
                 conn.execute("SELECT shop, day, amount, used, groups FROM budget_pools").fetchall()}
        held = {(shop, receipt): batch for batch, shop, receipt in
                conn.execute("SELECT batch_id, shop, receipt FROM budget_allocations WHERE status = 'HELD'")}
        outcome = adjudicate(result, pools, held)
        for row in outcome["receipts"]:
            if row["status"] in ("funded", "unfunded", "free"):
                conn.execute("INSERT INTO budget_allocations VALUES (?, ?, ?, ?, ?, ?)",
                             (meta["digest"], row["id"], row["shop"], row["day"], row["occupied"],
                              "HELD" if row["occupied"] else "SETTLED"))
        for (shop, day), amount in sorted(outcome["deltas"].items()):
            conn.execute("UPDATE budget_pools SET used = used + ? WHERE shop = ? AND day = ?",
                         (amount, shop, day))
        record = {"id": meta["digest"], "kind": "budget-batch", "manifest": meta, "sample": label,
                  "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                  "counts": result["receipts"]["counts"], "receipts": outcome["receipts"],
                  "pools": pools_view(conn, meta["digest"]),
                  **evaluate(outcome["receipts"], pool_findings(conn))}
        persist_record(conn, meta["digest"], source, record)
    return {**record, "reused": False}


def fetch(batch_id):
    with transaction() as conn:
        return load_record(conn, batch_id)


def pools_snapshot():
    with transaction() as conn:
        return {"pools": pools_view(conn, "")}


def void(batch_id, receipt_ids):
    with transaction() as conn:
        record = load_record(conn, batch_id)
        allocations = {row[0]: row for row in conn.execute(
            "SELECT receipt, shop, day, amount, status FROM budget_allocations WHERE batch_id = ?",
            (batch_id,)).fetchall()}
        released, already, unknown = [], [], []
        freed = {}
        for receipt in dict.fromkeys(receipt_ids):
            allocation = allocations.get(receipt)
            if allocation is None:
                unknown.append(receipt)
                continue
            _, shop, day, amount, status = allocation
            if status == "VOIDED":
                already.append(receipt)
                continue
            conn.execute("UPDATE budget_allocations SET status = 'VOIDED' WHERE batch_id = ? AND receipt = ?",
                         (batch_id, receipt))
            freed[shop, day] = freed.get((shop, day), 0) + amount
            released.append({"id": receipt, "amount": amount})
        for (shop, day), amount in sorted(freed.items()):
            conn.execute("UPDATE budget_pools SET used = used - ? WHERE shop = ? AND day = ?",
                         (amount, shop, day))
        let_go = {row["id"] for row in released}
        for row in record["receipts"]:
            if row["id"] in let_go:
                row.update(voided=True, released=row["occupied"], occupied=0)
        record.update(pools=pools_view(conn, batch_id),
                      **evaluate(record["receipts"], pool_findings(conn)))
        conn.execute("UPDATE budget_batches SET record = ? WHERE id = ?",
                     (canonical(record), batch_id))
    return {**record, "reused": False,
            "void": {"released": released, "already_void": already, "unknown": unknown}}
