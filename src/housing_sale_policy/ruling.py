"""项目裁定：由有效政策版本与项目时态事实得出分路结论。

裁定结果为三分路：可申请预售、现房备案，或需区政府评估和市级备案；
条件不满足时予以拦截并逐项给出原因。每次裁定都记录命中的政策日期
与所用材料，供市级复核逐项回放。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .policy import PolicyVersion
from .temporal import Fact, FactKind, FactLog


class Route(str, Enum):
    """裁定分路。"""

    PRESALE = "presale"  # 可申请预售
    EXISTING_HOME_FILING = "existing_home_filing"  # 现房备案
    DISTRICT_REVIEW = "district_review"  # 需区政府评估和市级备案
    REJECTED = "rejected"  # 条件不满足，予以拦截


@dataclass(frozen=True)
class PolicyHit:
    """裁定命中的政策日期。"""

    rule_id: str
    anchor: str  # 划分依据（事实种类或贷款合同等）
    cutoff: date  # 政策基准日
    project_date: date | None  # 项目对应日期
    outcome: str  # 命中结果说明


@dataclass(frozen=True)
class MaterialRef:
    """裁定所用的材料（时态事实）。"""

    fact_id: str
    kind: FactKind
    occurred_on: date
    source: str
    submitted_by: str


@dataclass(frozen=True)
class Ruling:
    """一次裁定结论；规则更正只产生新裁定并保留旧结论。"""

    ruling_id: str
    project_id: str
    policy_id: str
    project_version: int  # 基于的项目版本
    route: Route
    classification: str  # new 新项目 / in_transit 在途项目 / unknown 无法划分
    decided_on: date
    decided_by: str  # 审批人
    seq: int
    supersedes: str | None = None  # 被更正的旧裁定
    policy_hits: tuple[PolicyHit, ...] = ()
    materials: tuple[MaterialRef, ...] = ()
    intercepts: tuple[str, ...] = ()


def classify(
    log: FactLog, policy: PolicyVersion
) -> tuple[str, tuple[PolicyHit, ...], tuple[Fact, ...]]:
    """按政策版本的分类规则划分新项目与在途项目。"""
    hits: list[PolicyHit] = []
    used: list[Fact] = []
    saw_anchor = False
    for rule in policy.classification:
        fact = log.latest(rule.anchor)
        if fact is None:
            hits.append(
                PolicyHit(rule.rule_id, rule.anchor.value, rule.cutoff, None, "项目缺少该类事实")
            )
            continue
        saw_anchor = True
        used.append(fact)
        if fact.occurred_on >= rule.cutoff:
            hits.append(
                PolicyHit(
                    rule.rule_id,
                    rule.anchor.value,
                    rule.cutoff,
                    fact.occurred_on,
                    "不早于政策基准日，按新项目管理",
                )
            )
            return "new", tuple(hits), tuple(used)
        hits.append(
            PolicyHit(
                rule.rule_id,
                rule.anchor.value,
                rule.cutoff,
                fact.occurred_on,
                "早于政策基准日",
            )
        )
    return ("in_transit" if saw_anchor else "unknown"), tuple(hits), tuple(used)


def seizure_active(log: FactLog) -> Fact | None:
    """未解除的抵押查封事实；无或未解除标记时返回 None。"""
    fact = log.latest(FactKind.MORTGAGE_SEIZURE)
    if fact is not None and fact.detail.get("status") != "lifted":
        return fact
    return None


def adjudicate_project(
    *,
    log: FactLog,
    policy: PolicyVersion,
    supervision_opened: bool,
) -> tuple[Route, str, tuple[PolicyHit, ...], tuple[MaterialRef, ...], tuple[str, ...]]:
    """对项目当前事实做一次裁定，返回分路、分类、命中政策日期、材料与拦截原因。"""
    intercepts: list[str] = []
    classification, hits, class_facts = classify(log, policy)

    joint = log.latest(FactKind.JOINT_ACCEPTANCE)
    first_reg = log.latest(FactKind.FIRST_REGISTRATION)
    topping = log.latest(FactKind.TOPPING_OUT)
    permit = log.latest(FactKind.PRESALE_PERMIT)
    planning = log.latest(FactKind.PLANNING_PERMIT)
    seizure = seizure_active(log)

    # 事实矛盾检查：节点日期先后关系不可能成立时转人工评估
    contradictions: list[str] = []
    if joint is not None and topping is not None and joint.occurred_on < topping.occurred_on:
        contradictions.append("联合验收早于主体封顶，事实矛盾")
    if permit is not None and planning is not None and permit.occurred_on < planning.occurred_on:
        contradictions.append("预售许可早于规划许可，事实矛盾")

    used: list[Fact] = list(class_facts)
    for fact in (joint, first_reg, topping, permit, seizure):
        if fact is not None and fact not in used:
            used.append(fact)

    if contradictions:
        route = Route.DISTRICT_REVIEW
        intercepts.extend(contradictions)
        intercepts.append("事实矛盾，需区政府评估和市级备案")
    elif seizure is not None:
        route = Route.DISTRICT_REVIEW
        intercepts.append("存在未解除的抵押查封，需区政府评估和市级备案")
    elif joint is not None and first_reg is not None:
        route = Route.EXISTING_HOME_FILING
    elif classification == "unknown":
        route = Route.REJECTED
        intercepts.append("缺少土地公告或规划许可事实，无法划分新旧政策")
    elif classification == "new":
        if policy.presale_requires_topping_out and topping is None:
            intercepts.append("新项目预售要求主体封顶")
        if policy.presale_requires_supervision and not supervision_opened:
            intercepts.append("新项目预售要求资金全过程监管，监管账户未开立")
        route = Route.REJECTED if intercepts else Route.PRESALE
    else:
        route = Route.PRESALE

    materials = tuple(
        MaterialRef(f.fact_id, f.kind, f.occurred_on, f.source, f.submitted_by)
        for f in used
    )
    return route, classification, hits, materials, tuple(intercepts)
