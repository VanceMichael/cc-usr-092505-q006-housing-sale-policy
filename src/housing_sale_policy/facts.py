"""项目时态事实及其来源。

土地取得、公告、规划许可、主体封顶、分部验收、预售许可、首次登记、
抵押查封和联合验收都保存为带来源的时态事实；事实一经记录不可改写，
补证只能追加新事实，项目版本号随事实提交递增。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class FactKind(Enum):
    """需要保存的项目时态事实种类。"""

    LAND_ACQUISITION = "土地取得"
    LAND_ANNOUNCEMENT = "土地公告"
    PLANNING_PERMIT = "规划许可"
    LOAN_CONTRACT = "贷款合同"
    STRUCTURE_TOPPING = "主体封顶"
    PARTIAL_ACCEPTANCE = "分部验收"
    PRESALE_PERMIT = "预售许可"
    FIRST_REGISTRATION = "首次登记"
    MORTGAGE_SEIZURE = "抵押查封"
    JOINT_ACCEPTANCE = "联合验收"


@dataclass(frozen=True)
class EvidenceSource:
    """事实来源：材料编号、出具机构与提交人。"""

    document_no: str
    issuer: str
    submitted_by: str
    submitted_role: str


@dataclass(frozen=True)
class TemporalFact:
    """带来源的项目时态事实，一经记录不可改写。"""

    fact_id: str
    kind: FactKind
    occurred_on: date
    source: EvidenceSource
    recorded_at: datetime


@dataclass(frozen=True)
class MaterialRef:
    """裁定所引用材料的索引，供市级复核逐项核对。"""

    fact_id: str
    kind: FactKind
    occurred_on: date
    document_no: str
    issuer: str
    submitted_by: str

    @classmethod
    def of(cls, fact: TemporalFact) -> "MaterialRef":
        return cls(
            fact_id=fact.fact_id,
            kind=fact.kind,
            occurred_on=fact.occurred_on,
            document_no=fact.source.document_no,
            issuer=fact.source.issuer,
            submitted_by=fact.source.submitted_by,
        )


class FactView:
    """某一项目版本下的事实快照，只读。"""

    def __init__(self, facts: tuple[TemporalFact, ...]) -> None:
        self._facts = facts

    def all(self) -> tuple[TemporalFact, ...]:
        return self._facts

    def has(self, kind: FactKind) -> bool:
        return any(fact.kind is kind for fact in self._facts)

    def latest(self, kind: FactKind) -> TemporalFact | None:
        """返回最后记录的同类事实；补证以新记录为准。"""
        for fact in reversed(self._facts):
            if fact.kind is kind:
                return fact
        return None


class FactLog:
    """按项目追加保存事实；版本号等于已提交事实数。"""

    def __init__(self) -> None:
        self._facts: list[TemporalFact] = []

    @property
    def version(self) -> int:
        return len(self._facts)

    def append(self, fact: TemporalFact) -> None:
        self._facts.append(fact)

    def view(self) -> FactView:
        return FactView(tuple(self._facts))
