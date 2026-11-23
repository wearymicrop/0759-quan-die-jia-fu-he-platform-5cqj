import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.errors import DomainError
from app.modules.receipts.contract import Key
from app.modules.receipts.money import cents


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shop: Key
    day: str
    amount: str
    groups: Annotated[list[Key], Field(min_length=1, max_length=12)] = ["店券"]

    @field_validator("day")
    @classmethod
    def trade_day(cls, value):
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("交易日须为 YYYY-MM-DD")
        from datetime import date
        date.fromisoformat(value)
        return value


class BudgetBook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budgets: Annotated[list[Budget], Field(max_length=400)]


def declared(raw):
    try:
        book = BudgetBook.model_validate({"budgets": raw}).model_dump()
    except ValidationError as exc:
        raise DomainError("BAD_BUDGET", "预算池声明的字段无效") from exc
    pools = {}
    for row in book["budgets"]:
        key = (row["shop"], row["day"])
        if key in pools:
            raise DomainError("BAD_BUDGET", "同一门店同一交易日预算重复声明")
        try:
            amount = cents(row["amount"])
        except ValueError as exc:
            raise DomainError("BAD_BUDGET", "预算金额须为最多两位小数的人民币") from exc
        pools[key] = {"amount": amount, "groups": sorted(set(row["groups"]))}
    return pools
