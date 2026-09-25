"""政策版本：按生效日选择版本，按项目事实日期划分适用规则。

新政策以土地公告、规划许可等不同日期划分新项目与在途项目，
因此每个政策版本携带一组分类规则；规则更正只新增版本，旧版本保留。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .temporal import FactKind


@dataclass(frozen=True)
class ClassificationRule:
    """新旧项目划分规则：项目 anchor 事实日期不早于 cutoff 时命中。"""

    rule_id: str
    anchor: FactKind  # 以哪类事实的日期划分（土地公告、规划许可等）
    cutoff: date  # 政策基准日


@dataclass(frozen=True)
class PolicyVersion:
    """一个有效政策版本，字段即该版本下的裁定参数。"""

    policy_id: str
    effective_from: date  # 生效日
    classification: tuple[ClassificationRule, ...]  # 任一命中即为新项目
    presale_requires_topping_out: bool  # 新项目预售须主体封顶
    presale_requires_supervision: bool  # 新项目预售须资金全过程监管
    deposit_cap_ratio: Decimal  # 现房定金上限（占房屋价款比例）
    mortgage_supervised_contract_cutoff: date | None  # 贷款合同日不早于该日时按揭须入监管账户


class PolicyBook:
    """政策版本簿：版本只增不改，按生效日取有效版本。"""

    def __init__(self, versions: Iterable[PolicyVersion]) -> None:
        items = tuple(versions)
        if not items:
            raise ValueError("至少需要一个政策版本")
        if len({item.policy_id for item in items}) != len(items):
            raise ValueError("政策版本编号重复")
        self._versions = tuple(sorted(items, key=lambda item: item.effective_from))

    @property
    def versions(self) -> tuple[PolicyVersion, ...]:
        return self._versions

    def get(self, policy_id: str) -> PolicyVersion:
        """按编号取政策版本（用于还原某次裁定当时的资格参数）。"""
        for item in self._versions:
            if item.policy_id == policy_id:
                return item
        raise KeyError(policy_id)

    def effective_on(self, day: date) -> PolicyVersion:
        """day 当日有效的政策版本。"""
        chosen: PolicyVersion | None = None
        for item in self._versions:
            if item.effective_from <= day:
                chosen = item
        if chosen is None:
            raise ValueError(f"{day.isoformat()} 之前无有效政策版本")
        return chosen
