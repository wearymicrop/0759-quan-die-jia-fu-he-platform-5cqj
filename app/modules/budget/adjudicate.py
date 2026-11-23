from app.modules.optimizer.allocation import simulate
from app.modules.optimizer.solver import combos
from app.modules.refunds.replay import day


def views(qualification, gross, groups):
    rules = sorted(qualification["qualified"], key=lambda rule: (rule["rank"], rule["code"]))
    allowed = {(edge["a"], edge["b"]) for edge in qualification["matrix"] if edge["reason"] == "COMPATIBLE"}
    group_of = {rule["code"]: rule["group"] for rule in rules}
    ranked = []
    for combo in combos(rules, allowed):
        net, applied = simulate(gross, combo)
        applied = [{**item, "funded": group_of[item["code"]] in groups} for item in applied]
        funded = sum(item["amount"] for item in applied if item["funded"])
        codes = [rule["code"] for rule in combo]
        ranked.append({"codes": codes, "due": sum(net.values()), "net": net, "applied": applied, "funded": funded})
    ranked.sort(key=lambda row: (row["due"], len(row["codes"]), sorted(row["codes"])))
    return ranked


def adjudicate(result, pools, held):
    """按到达顺序裁决每张小票的预算出资。

    pools: {(shop, day): {"amount", "used", "groups"}}，used 为批次前已占用。
    held: {(shop, receipt): batch_id}，其他批次仍持有的占用。
    返回 {"receipts": [...], "deltas": {(shop, day): 本批新增占用}}。
    """
    groups_all = sorted({group for pool in pools.values() for group in pool["groups"]})
    sales = {row["id"]: row for row in result["receipts"]["sales"]}
    used = {key: pool["used"] for key, pool in pools.items()}
    receipts = []
    ordered = sorted(result["optimizer"], key=lambda row: (sales[row["id"]]["at"], row["id"]))
    for solution in ordered:
        sale = sales[solution["id"]]
        key = (sale["shop"], day(sale["at"]))
        base = {"id": sale["id"], "shop": sale["shop"], "day": key[1], "at": sale["at"],
                "source_line": sale["source_line"], "paid": sale["paid"], "voided": False,
                "released": 0, "occupied": 0}
        if (sale["shop"], sale["id"]) in held:
            receipts.append({**base, "status": "duplicate", "reason": "DUPLICATE_DELIVERY",
                             "original_batch": held[sale["shop"], sale["id"]]})
            continue
        if solution["status"] != "solved":
            receipts.append({**base, "status": "isolated", "reason": solution["reason"]})
            continue
        pool = pools.get(key)
        groups = pool["groups"] if pool else groups_all
        remaining = pool["amount"] - used[key] if pool else 0
        gross = {line["id"]: line["qty"] * line["price"] for line in sale["lines"]}
        ranked = views(result["eligibility"][sale["id"]], gross, groups)
        best = ranked[0]
        chosen = next(row for row in ranked if row["funded"] <= remaining)
        reason = dropped = None
        if chosen is not best:
            reason = "BUDGET_EXHAUSTED" if pool else "POOL_MISSING"
            kept = {item["code"] for item in chosen["applied"] if item["funded"]}
            dropped = sorted({item["code"] for item in best["applied"] if item["funded"]} - kept)
        occupied = chosen["funded"]
        if pool:
            used[key] += occupied
        receipts.append({**base, "status": "funded" if occupied else ("unfunded" if reason else "free"),
                         "reason": reason, "combo": chosen["codes"], "applied": chosen["applied"],
                         "gross": sum(gross.values()), "discount": sum(gross.values()) - chosen["due"],
                         "due": chosen["due"], "gap": sale["paid"] - chosen["due"],
                         "occupied": occupied, "best_combo": best["codes"], "best_due": best["due"],
                         "dropped": dropped or []})
    deltas = {key: used[key] - pool["used"] for key, pool in pools.items() if used[key] != pool["used"]}
    return {"receipts": receipts, "deltas": deltas}


def evaluate(receipts, pool_findings):
    """三道闸门：券组合、预算占用、实付互相对上才允许进入正式批次。"""
    combo = []
    paid = []
    for row in receipts:
        if row["status"] in ("isolated", "duplicate") or row["voided"]:
            continue
        if sum(item["amount"] for item in row["applied"]) != row["gross"] - row["due"]:
            combo.append({"id": row["id"], "reason": "COMBO_IMBALANCE"})
        funded = sum(item["amount"] for item in row["applied"] if item["funded"])
        if funded != row["occupied"] + row["released"]:
            combo.append({"id": row["id"], "reason": "FUNDING_IMBALANCE"})
        if row["gap"]:
            paid.append({"id": row["id"], "reason": "PAID_MISMATCH", "gap": row["gap"]})
    gates = [{"check": "COMBO_BALANCE", "findings": combo, "status": "PASS" if not combo else "FAIL"},
             {"check": "POOL_BALANCE", "findings": pool_findings, "status": "PASS" if not pool_findings else "FAIL"},
             {"check": "PAID_MATCH", "findings": paid, "status": "PASS" if not paid else "FAIL"}]
    return {"gates": gates, "status": "formal" if all(gate["status"] == "PASS" for gate in gates) else "pending"}
