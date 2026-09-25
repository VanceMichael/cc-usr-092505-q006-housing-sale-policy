"""主办银行项目账本：只增不改。

开发贷、自有资金、预售款、现房定金和受托支付都记为账本分录，
分录一经确认不可改写，纠错只能追加反向分录。每笔分录引用产生
它的裁定编号，使资金事实始终可追溯到当时资格。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class LedgerKind(Enum):
    """项目账本分录种类。"""

    DEVELOP_LOAN = "开发贷"
    OWN_FUNDS = "自有资金"
    PRESALE_FUNDS = "预售款"
    EXISTING_DEPOSIT = "现房定金"
    ENTRUSTED_PAYMENT = "受托支付"
    MORTGAGE_DISBURSEMENT = "按揭放款"
    SUPERVISION_RELEASE = "监管解除"


class Direction(Enum):
    IN = "入账"
    OUT = "出账"


IN_KINDS = frozenset({
    LedgerKind.DEVELOP_LOAN,
    LedgerKind.OWN_FUNDS,
    LedgerKind.PRESALE_FUNDS,
    LedgerKind.EXISTING_DEPOSIT,
    LedgerKind.MORTGAGE_DISBURSEMENT,
})
OUT_KINDS = frozenset({LedgerKind.ENTRUSTED_PAYMENT, LedgerKind.SUPERVISION_RELEASE})


def direction_of(kind: LedgerKind) -> Direction:
    return Direction.OUT if kind in OUT_KINDS else Direction.IN


@dataclass(frozen=True)
class LedgerEntry:
    """账本分录：金额、方向、引用裁定与银行确认人。"""

    entry_id: str
    project_id: str
    kind: LedgerKind
    direction: Direction
    amount: Decimal
    adjudication_id: str
    confirmed_by: str
    project_version: int
    created_at: datetime
    declared_total_price: Decimal | None = None


class ProjectLedger:
    """只增账本：不提供修改与删除，余额由分录折叠得出。"""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    @property
    def count(self) -> int:
        return len(self._entries)

    def append(self, entry: LedgerEntry) -> None:
        if entry.amount <= 0:
            raise ValueError("分录金额必须为正")
        self._entries.append(entry)

    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def balance(self) -> Decimal:
        """监管账户余额：入账减出账。"""
        total = Decimal("0")
        for entry in self._entries:
            total += entry.amount if entry.direction is Direction.IN else -entry.amount
        return total

    def total(self, kind: LedgerKind) -> Decimal:
        return sum((e.amount for e in self._entries if e.kind is kind), Decimal("0"))

    def total_declared_price(self) -> Decimal:
        """现房定金分录累计声明的房屋总价，用于校验定金上限。"""
        return sum(
            (e.declared_total_price for e in self._entries
             if e.kind is LedgerKind.EXISTING_DEPOSIT and e.declared_total_price is not None),
            Decimal("0"),
        )
