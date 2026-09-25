"""政策版本、新旧项目分流与资格评估。

新政策按土地公告时间区分新项目与在途项目：土地公告日不早于政策
生效日的为新项目，预售要求主体封顶并实施资金全过程监管；早于生效
日的为在途项目，需区政府评估并报市级备案。已完成联合验收或首次
登记的项目转入现房备案。规则更正只能登记新政策版本，旧版本保留。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from .facts import FactKind, FactView, MaterialRef


class ProjectClass(Enum):
    """按政策生效日划分的项目类别。"""

    NEW = "新项目"
    IN_TRANSITION = "在途项目"


class Eligibility(Enum):
    """项目资格结论。"""

    PRESALE = "可申请预售"
    EXISTING_HOME = "现房备案"
    DISTRICT_REVIEW = "需区政府评估和市级备案"
    NOT_ELIGIBLE = "暂不具备条件"


@dataclass(frozen=True)
class PolicyVersion:
    """一个生效的政策版本；规则更正只能登记新版本，旧版本保留。

    更正版本以 corrects 指明被更正的版本号：被更正的版本保留在册但
    不再参与选用，更正版本按自己的生效日追溯适用。
    """

    policy_id: str
    version: int
    effective_from: date
    deposit_cap_ratio: Decimal
    presale_requires_topping: bool = True
    release_requires_joint_acceptance: bool = True
    full_process_supervision: bool = True
    corrects: int | None = None


@dataclass(frozen=True)
class PolicyHit:
    """一次裁定命中的政策版本与用于分流的关键日期。"""

    policy_id: str
    policy_version: int
    effective_from: date
    classification: ProjectClass | None
    anchor_dates: tuple[tuple[str, date], ...]


class PolicyBook:
    """按生效日期保存政策版本，取用时选择当时有效的版本。"""

    def __init__(self) -> None:
        self._versions: list[PolicyVersion] = []

    def register(self, policy: PolicyVersion) -> None:
        self._versions.append(policy)

    def effective(self, as_of: date) -> PolicyVersion:
        corrected = {
            (p.policy_id, p.corrects) for p in self._versions if p.corrects is not None
        }
        candidates = [
            p
            for p in self._versions
            if p.effective_from <= as_of and (p.policy_id, p.version) not in corrected
        ]
        if not candidates:
            raise LookupError("无有效政策版本")
        return max(candidates, key=lambda p: (p.effective_from, p.version))


@dataclass(frozen=True)
class EligibilityReport:
    """资格评估结果：结论、命中政策、所用材料与原因说明。"""

    outcome: Eligibility
    policy_hit: PolicyHit
    materials: tuple[MaterialRef, ...]
    reasons: tuple[str, ...]


def evaluate_eligibility(view: FactView, policy: PolicyVersion, as_of: date) -> EligibilityReport:
    """按有效政策版本评估项目资格。

    抵押查封在身的项目禁止销售动作；已完成联合验收或首次登记的转入
    现房备案；其余按土地公告日与政策生效日分流新项目与在途项目。
    """
    seizure = view.latest(FactKind.MORTGAGE_SEIZURE)
    if seizure is not None and seizure.occurred_on <= as_of:
        hit = PolicyHit(policy.policy_id, policy.version, policy.effective_from, None, ())
        return EligibilityReport(
            Eligibility.NOT_ELIGIBLE,
            hit,
            (MaterialRef.of(seizure),),
            (f"存在抵押查封事实（{seizure.occurred_on.isoformat()}），禁止销售动作",),
        )

    completed = [
        fact
        for kind in (FactKind.JOINT_ACCEPTANCE, FactKind.FIRST_REGISTRATION)
        if (fact := view.latest(kind)) is not None and fact.occurred_on <= as_of
    ]
    if completed:
        hit = PolicyHit(policy.policy_id, policy.version, policy.effective_from, None, ())
        materials = tuple(MaterialRef.of(fact) for fact in completed)
        return EligibilityReport(Eligibility.EXISTING_HOME, hit, materials, ())

    announcement = view.latest(FactKind.LAND_ANNOUNCEMENT)
    if announcement is None:
        hit = PolicyHit(policy.policy_id, policy.version, policy.effective_from, None, ())
        return EligibilityReport(
            Eligibility.NOT_ELIGIBLE, hit, (), ("缺少土地公告事实，无法划分新项目与在途项目",)
        )

    anchor_dates = [("土地公告日", announcement.occurred_on)]
    for kind, label in ((FactKind.PLANNING_PERMIT, "规划许可日"), (FactKind.LOAN_CONTRACT, "贷款合同日")):
        fact = view.latest(kind)
        if fact is not None:
            anchor_dates.append((label, fact.occurred_on))

    if announcement.occurred_on >= policy.effective_from:
        hit = PolicyHit(
            policy.policy_id, policy.version, policy.effective_from,
            ProjectClass.NEW, tuple(anchor_dates),
        )
        materials = [MaterialRef.of(announcement)]
        topping = view.latest(FactKind.STRUCTURE_TOPPING)
        if policy.presale_requires_topping and (topping is None or topping.occurred_on > as_of):
            return EligibilityReport(
                Eligibility.NOT_ELIGIBLE, hit, tuple(materials),
                ("新项目未达到主体封顶，不得申请预售",),
            )
        if topping is not None:
            materials.append(MaterialRef.of(topping))
        return EligibilityReport(Eligibility.PRESALE, hit, tuple(materials), ())

    hit = PolicyHit(
        policy.policy_id, policy.version, policy.effective_from,
        ProjectClass.IN_TRANSITION, tuple(anchor_dates),
    )
    return EligibilityReport(
        Eligibility.DISTRICT_REVIEW,
        hit,
        (MaterialRef.of(announcement),),
        ("在途项目需区政府评估并报市级备案后另行裁定",),
    )
