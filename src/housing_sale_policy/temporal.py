"""项目时态事实：带来源、只追加、可更正。

土地取得、公告、规划许可、主体封顶、分部验收、预售许可、首次登记、
抵押查封和联合验收都保存为时态事实：occurred_on 是业务发生日，
seq 是入库顺序，source 记录来源，更正以新事实链接旧事实并保留旧事实。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class FactKind(str, Enum):
    """须保存为时态事实的项目节点。"""

    LAND_ACQUIRED = "land_acquired"  # 土地取得
    LAND_ANNOUNCEMENT = "land_announcement"  # 土地公告
    PLANNING_PERMIT = "planning_permit"  # 规划许可
    TOPPING_OUT = "topping_out"  # 主体封顶
    SECTION_ACCEPTANCE = "section_acceptance"  # 分部验收
    PRESALE_PERMIT = "presale_permit"  # 预售许可
    FIRST_REGISTRATION = "first_registration"  # 首次登记
    MORTGAGE_SEIZURE = "mortgage_seizure"  # 抵押查封
    JOINT_ACCEPTANCE = "joint_acceptance"  # 联合验收


@dataclass(frozen=True)
class Fact:
    """一条带来源的时态事实。"""

    fact_id: str
    project_id: str
    kind: FactKind
    occurred_on: date  # 业务发生日（时态）
    source: str  # 来源：文号、业务系统或提交说明
    submitted_by: str  # 材料提交人
    seq: int  # 入库序号（系统时间）
    corrects: str | None = None  # 被更正的事实编号
    detail: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))


class FactLog:
    """只追加的事实日志：更正产生新事实并保留旧事实。"""

    def __init__(self) -> None:
        self._facts: list[Fact] = []

    def append(self, fact: Fact) -> Fact:
        if any(item.fact_id == fact.fact_id for item in self._facts):
            raise ValueError(f"事实编号重复: {fact.fact_id}")
        if fact.corrects is not None and not any(
            item.fact_id == fact.corrects for item in self._facts
        ):
            raise ValueError(f"被更正的事实不存在: {fact.corrects}")
        self._facts.append(fact)
        return fact

    def __len__(self) -> int:
        return len(self._facts)

    def all(self) -> tuple[Fact, ...]:
        return tuple(self._facts)

    def get(self, fact_id: str) -> Fact:
        for item in self._facts:
            if item.fact_id == fact_id:
                return item
        raise KeyError(fact_id)

    def is_corrected(self, fact_id: str) -> bool:
        return any(item.corrects == fact_id for item in self._facts)

    def active(self, kind: FactKind) -> tuple[Fact, ...]:
        """该种类下未被更正的事实，按业务日期与入库序排列。"""
        items = [
            item
            for item in self._facts
            if item.kind == kind and not self.is_corrected(item.fact_id)
        ]
        items.sort(key=lambda item: (item.occurred_on, item.seq))
        return tuple(items)

    def latest(self, kind: FactKind) -> Fact | None:
        """该种类下最新有效事实；无则返回 None。"""
        items = self.active(kind)
        return items[-1] if items else None
