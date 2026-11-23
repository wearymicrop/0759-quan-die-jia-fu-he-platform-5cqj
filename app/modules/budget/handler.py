import json

from fastapi import APIRouter

from app.config import ROOT
from app.errors import DomainError
from app.modules.budget import ledger

router = APIRouter()

FIELDS = ("events", "rules", "budgets")


def load_inputs(sample):
    folder = ROOT / "sample_data" / sample
    if not folder.is_dir() or not all((folder / name).exists() for name in
                                      ("events.jsonl", "rules.json", "budgets.json")):
        raise DomainError("SAMPLE_NOT_FOUND", "找不到指定样例", 404)
    return {"events": (folder / "events.jsonl").read_bytes().decode("utf-8"),
            "rules": json.loads((folder / "rules.json").read_text()),
            "budgets": json.loads((folder / "budgets.json").read_text())}


def choose(payload):
    if set(payload) == {"sample"} and isinstance(payload["sample"], str):
        return load_inputs(payload["sample"]), payload["sample"]
    if set(payload) == set(FIELDS):
        events, rules, budgets = (payload[name] for name in FIELDS)
        if isinstance(events, str) and isinstance(rules, dict) and isinstance(budgets, list):
            if len(events.encode("utf-8")) > 400_000:
                raise DomainError("INPUT_LIMIT", "流水文本超过四十万字节")
            return payload, "自备数据"
    raise DomainError("CHOOSE_SOURCE", "请选择样例或提供流水、规则集与预算池")


@router.get("/api/budgets/inputs/{sample}")
def inputs(sample: str):
    return load_inputs(sample)


@router.post("/api/budgets/batches")
def submit(payload: dict):
    source, label = choose(payload)
    return ledger.commit(source, label)


@router.get("/api/budgets/batches/{batch_id}")
def saved(batch_id: str):
    return ledger.fetch(batch_id)


@router.post("/api/budgets/batches/{batch_id}/void")
def cancel(batch_id: str, payload: dict):
    receipts = payload.get("receipts") if set(payload) == {"receipts"} else None
    if not isinstance(receipts, list) or not 0 < len(receipts) <= 400 \
            or not all(isinstance(item, str) and item.strip() for item in receipts):
        raise DomainError("BAD_VOID", "作废请求须为一组小票号")
    return ledger.void(batch_id, receipts)


@router.get("/api/budgets/pools")
def pools():
    return ledger.pools_snapshot()
