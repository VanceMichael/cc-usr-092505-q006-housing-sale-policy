"""主办银行项目账本：只追加、不可改写，更正以冲正条目完成。

开发贷、自有资金、预售款、现房定金和受托支付等资金事实由主办银行确认后
入账；账本不提供修改接口，任何更正都追加冲正条目并保留原条目。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class FundKind(str, Enum):
    """项目账本的资金种类。"""

    DEVELOPMENT_LOAN = "development_loan"  # 开发贷
    OWN_FUNDS = "own_funds"  # 自有资金
    PRESALE_PROCEEDS = "presale_proceeds"  # 预售款
    EXISTING_HOME_DEPOSIT = "existing_home_deposit"  # 现房定金
    ENTRUSTED_PAYMENT = "entrusted_payment"  # 受托支付
    MORTGAGE_DISBURSEMENT = "mortgage_disbursement"  # 按揭放款
    SUPERVISION_RELEASE = "supervision_release"  # 监管解除
    REVERSAL = "reversal"  # 冲正


# 纳入监管账户余额的流向
SUPERVISED_INFLOW = frozenset(
    {FundKind.PRESALE_PROCEEDS, FundKind.MORTGAGE_DISBURSEMENT}
)
SUPERVISED_OUTFLOW = frozenset(
    {FundKind.ENTRUSTED_PAYMENT, FundKind.SUPERVISION_RELEASE}
)


@dataclass(frozen=True)
class LedgerEntry:
    """一条资金事实；amount 恒为正，方向由种类决定，冲正条目抵销原条目。"""

    entry_id: str
    project_id: str
    kind: FundKind
    amount: Decimal
    occurred_on: date  # 业务发生日
    confirmed_by: str  # 确认资金事实的主办银行
    seq: int  # 入账序号
    ruling_id: str | None = None  # 引用的当时资格（定金、按揭放款、监管解除等）
    reverses: str | None = None  # 被冲正的条目编号
    detail: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))
        if self.amount <= 0:
            raise ValueError("金额必须为正")


class ProjectLedger:
    """单项目只追加账本：不提供修改，更正以冲正条目完成。"""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self._entries: list[LedgerEntry] = []

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        if entry.project_id != self.project_id:
            raise ValueError("账目不属于本项目账本")
        if any(item.entry_id == entry.entry_id for item in self._entries):
            raise ValueError(f"账目编号重复: {entry.entry_id}")
        if entry.kind is FundKind.REVERSAL:
            if entry.reverses is None:
                raise ValueError("冲正条目必须指明被冲正条目")
            target = self.get(entry.reverses)
            if target.kind is FundKind.REVERSAL:
                raise ValueError("冲正条目不得再被冲正")
            if self.is_reversed(target.entry_id):
                raise ValueError(f"条目已被冲正: {target.entry_id}")
        elif entry.reverses is not None:
            raise ValueError("非冲正条目不得指向其他条目")
        self._entries.append(entry)
        return entry

    def __len__(self) -> int:
        return len(self._entries)

    def all(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def get(self, entry_id: str) -> LedgerEntry:
        for item in self._entries:
            if item.entry_id == entry_id:
                return item
        raise KeyError(entry_id)

    def is_reversed(self, entry_id: str) -> bool:
        return any(item.reverses == entry_id for item in self._entries)

    def supervised_effect(self, entry: LedgerEntry) -> Decimal:
        """条目对监管账户余额的影响。"""
        if entry.kind in SUPERVISED_INFLOW:
            return entry.amount
        if entry.kind in SUPERVISED_OUTFLOW:
            return -entry.amount
        return Decimal(0)

    def supervised_balance(self) -> Decimal:
        """监管账户可用余额（含冲正抵销）。"""
        total = Decimal(0)
        for item in self._entries:
            if item.kind is FundKind.REVERSAL:
                total -= self.supervised_effect(self.get(item.reverses))  # type: ignore[arg-type]
            else:
                total += self.supervised_effect(item)
        return total

    def total_of(self, kind: FundKind) -> Decimal:
        """某种类资金的净额（含冲正抵销）。"""
        total = Decimal(0)
        for item in self._entries:
            if item.kind is kind:
                total += item.amount
            elif item.kind is FundKind.REVERSAL and self.get(
                item.reverses  # type: ignore[arg-type]
            ).kind is kind:
                total -= item.amount
        return total

    def supervision_released(self) -> bool:
        """监管是否已解除（存在未被冲正的监管解除条目）。"""
        return any(
            item.kind is FundKind.SUPERVISION_RELEASE
            and not self.is_reversed(item.entry_id)
            for item in self._entries
        )
