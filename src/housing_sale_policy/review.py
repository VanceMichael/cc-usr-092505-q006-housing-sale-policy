"""市级复核：逐项列出裁定依据。

复核人员抽取任一项目和资金动作时，逐项列出命中的政策日期、所用
材料、额度变化与拦截原因，而不是只返回允许或拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .adjudication import QuotaChange
from .facts import MaterialRef
from .policy import PolicyHit
from .service import AdjudicationService


@dataclass(frozen=True)
class ActionExplanation:
    """一条裁定的逐项解释。"""

    adjudication_id: str
    project_id: str
    project_version: int
    subject: str
    outcome: str
    policy_hits: tuple[PolicyHit, ...]
    materials: tuple[MaterialRef, ...]
    quota_changes: tuple[QuotaChange, ...]
    intercepts: tuple[str, ...]
    references: tuple[str, ...]
    ledger_entry_id: str | None
    confirmed_by: str | None
    supersedes: str | None


@dataclass(frozen=True)
class ProjectExplanation:
    """一个项目的完整复核视图。"""

    project_id: str
    fact_version: int
    supervised_balance: Decimal
    actions: tuple[ActionExplanation, ...]


def explain_action(
    service: AdjudicationService, project_id: str, adjudication_id: str
) -> ActionExplanation:
    """逐项解释一条裁定：政策日期、材料、额度变化与拦截原因。"""
    adjudication = service.adjudication(project_id, adjudication_id)
    entry = service.entry_for_adjudication(project_id, adjudication_id)
    return ActionExplanation(
        adjudication_id=adjudication.adjudication_id,
        project_id=project_id,
        project_version=adjudication.project_version,
        subject=adjudication.subject,
        outcome=adjudication.outcome.value,
        policy_hits=adjudication.policy_hits,
        materials=adjudication.materials,
        quota_changes=adjudication.quota_changes,
        intercepts=adjudication.intercepts,
        references=adjudication.references,
        ledger_entry_id=entry.entry_id if entry is not None else None,
        confirmed_by=entry.confirmed_by if entry is not None else None,
        supersedes=adjudication.supersedes,
    )


def explain_project(service: AdjudicationService, project_id: str) -> ProjectExplanation:
    """列出一个项目的全部裁定与当前账本状态。"""
    actions = tuple(
        explain_action(service, project_id, adjudication.adjudication_id)
        for adjudication in service.adjudications(project_id)
    )
    return ProjectExplanation(
        project_id=project_id,
        fact_version=service.fact_version(project_id),
        supervised_balance=service.ledger_balance(project_id),
        actions=actions,
    )
