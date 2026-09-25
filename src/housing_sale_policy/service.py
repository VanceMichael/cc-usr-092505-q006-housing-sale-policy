"""商品住房销售裁定应用服务。

职责分离：开发企业只能提交本项目材料，主办银行确认资金事实，审批人
与材料提交人不得相同。每个项目一把锁：补证与付款并发时，各操作在
同一项目版本上完整裁定并落账，互不撕裂。资格裁定以项目事实版本为
准，补证后须重新裁定；规则更正只能产生新裁定并保留旧结论。
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Callable

from .adjudication import (
    ELIGIBILITY_TO_OUTCOME,
    SUBJECT_ELIGIBILITY,
    Adjudication,
    FundAction,
    Outcome,
    adjudicate_fund_action,
)
from .facts import EvidenceSource, FactKind, FactLog, TemporalFact
from .ledger import LedgerEntry, LedgerKind, ProjectLedger, direction_of
from .policy import PolicyBook, PolicyVersion, evaluate_eligibility

SCOPE_SUPERVISION_RELEASE = "监管解除"


class Role(Enum):
    """参与方角色。"""

    DEVELOPER = "开发企业"
    BANK = "主办银行"
    CASE_OFFICER = "住房管理受理人员"
    REVIEWER = "项目复核人员"


@dataclass(frozen=True)
class Actor:
    """操作人：角色与所属机构。"""

    actor_id: str
    role: Role
    org_id: str


class AdjudicationError(Exception):
    """裁定服务异常基类。"""


class PermissionDenied(AdjudicationError):
    """越权操作。"""


class NotFound(AdjudicationError):
    """项目或裁定不存在。"""


class StateError(AdjudicationError):
    """当前状态不允许该操作。"""


@dataclass(frozen=True)
class Approval:
    """受理人员审批记录。"""

    approval_id: str
    project_id: str
    scope: str
    approver: str
    approved_at: datetime


@dataclass
class _Project:
    """项目聚合：事实、账本、裁定与审批，一把锁保护并发。"""

    project_id: str
    name: str
    developer_id: str
    bank_id: str
    facts: FactLog = field(default_factory=FactLog)
    ledger: ProjectLedger = field(default_factory=ProjectLedger)
    adjudications: list[Adjudication] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class AdjudicationService:
    """商品住房销售裁定服务。"""

    def __init__(self, policies: PolicyBook, clock: Callable[[], datetime] = datetime.now) -> None:
        self._policies = policies
        self._clock = clock
        self._projects: dict[str, _Project] = {}
        self._ids = itertools.count(1)
        self._id_lock = threading.Lock()

    def _next_id(self, prefix: str) -> str:
        with self._id_lock:
            return f"{prefix}-{next(self._ids):06d}"

    def register_project(self, project_id: str, name: str, developer_id: str, bank_id: str) -> None:
        if project_id in self._projects:
            raise StateError(f"项目已存在：{project_id}")
        self._projects[project_id] = _Project(project_id, name, developer_id, bank_id)

    def register_policy(self, policy: PolicyVersion) -> None:
        """登记政策版本；规则更正只能新增版本，旧版本保留。"""
        self._policies.register(policy)

    def _project(self, project_id: str) -> _Project:
        try:
            return self._projects[project_id]
        except KeyError:
            raise NotFound(f"项目不存在：{project_id}") from None

    # ---- 材料提交 ----

    def submit_fact(
        self,
        project_id: str,
        kind: FactKind,
        occurred_on: date,
        document_no: str,
        issuer: str,
        actor: Actor,
    ) -> str:
        """提交项目时态事实；开发企业只能提交本项目材料。"""
        project = self._project(project_id)
        if actor.role is Role.DEVELOPER:
            if actor.org_id != project.developer_id:
                raise PermissionDenied("开发企业只能提交本项目材料")
        elif actor.role is not Role.CASE_OFFICER:
            raise PermissionDenied("该角色不能提交项目材料")
        with project.lock:
            fact = TemporalFact(
                fact_id=self._next_id("F"),
                kind=kind,
                occurred_on=occurred_on,
                source=EvidenceSource(document_no, issuer, actor.actor_id, actor.role.value),
                recorded_at=self._clock(),
            )
            project.facts.append(fact)
            return fact.fact_id

    # ---- 资格裁定 ----

    def _check_project_party(self, project: _Project, actor: Actor) -> None:
        if actor.role is Role.DEVELOPER:
            if actor.org_id != project.developer_id:
                raise PermissionDenied("开发企业只能查询本项目资格")
        elif actor.role is not Role.CASE_OFFICER:
            raise PermissionDenied("该角色不能发起资格裁定")

    def _assess_locked(
        self, project: _Project, actor: Actor, as_of: date, supersedes: str | None
    ) -> Adjudication:
        policy = self._policies.effective(as_of)
        report = evaluate_eligibility(project.facts.view(), policy, as_of)
        adjudication = Adjudication(
            adjudication_id=self._next_id("A"),
            project_id=project.project_id,
            project_version=project.facts.version,
            subject=SUBJECT_ELIGIBILITY,
            outcome=ELIGIBILITY_TO_OUTCOME[report.outcome],
            policy_hits=(report.policy_hit,),
            materials=report.materials,
            quota_changes=(),
            intercepts=report.reasons,
            references=(),
            requested_by=actor.actor_id,
            created_at=self._clock(),
            supersedes=supersedes,
        )
        project.adjudications.append(adjudication)
        return adjudication

    def assess_eligibility(self, project_id: str, actor: Actor, as_of: date) -> Adjudication:
        """按有效政策版本评估项目资格。"""
        project = self._project(project_id)
        self._check_project_party(project, actor)
        with project.lock:
            return self._assess_locked(project, actor, as_of, supersedes=None)

    def correct_eligibility(self, project_id: str, actor: Actor, as_of: date) -> Adjudication:
        """规则更正：以当前有效政策重新裁定，旧结论保留可查。"""
        project = self._project(project_id)
        if actor.role is not Role.CASE_OFFICER:
            raise PermissionDenied("规则更正只能由住房管理受理人员办理")
        with project.lock:
            previous = self._latest_eligibility_locked(project)
            if previous is None:
                raise NotFound("无旧资格裁定可更正")
            return self._assess_locked(project, actor, as_of, supersedes=previous.adjudication_id)

    def _latest_eligibility_locked(self, project: _Project) -> Adjudication | None:
        for adjudication in reversed(project.adjudications):
            if adjudication.subject == SUBJECT_ELIGIBILITY:
                return adjudication
        return None

    def current_eligibility(self, project_id: str) -> Adjudication | None:
        project = self._project(project_id)
        with project.lock:
            return self._latest_eligibility_locked(project)

    # ---- 审批 ----

    def approve_supervision_release(self, project_id: str, officer: Actor) -> Approval:
        """审批监管解除；审批人与任一材料提交人不得相同。"""
        project = self._project(project_id)
        if officer.role is not Role.CASE_OFFICER:
            raise PermissionDenied("只有住房管理受理人员可以审批监管解除")
        with project.lock:
            submitters = {fact.source.submitted_by for fact in project.facts.view().all()}
            if officer.actor_id in submitters:
                raise PermissionDenied("审批人与材料提交人不得相同")
            if any(a.scope == SCOPE_SUPERVISION_RELEASE for a in project.approvals):
                raise StateError("监管解除已审批")
            approval = Approval(
                approval_id=self._next_id("AP"),
                project_id=project_id,
                scope=SCOPE_SUPERVISION_RELEASE,
                approver=officer.actor_id,
                approved_at=self._clock(),
            )
            project.approvals.append(approval)
            return approval

    # ---- 资金动作 ----

    def request_fund_action(
        self, project_id: str, action: FundAction, actor: Actor, as_of: date
    ) -> Adjudication:
        """裁定并确认资金动作；资金事实只能由主办银行确认。

        裁定与入账在同一项目锁内完成：允许则追加账本分录，拒绝事项
        不占用任何额度。定金上限、按揭放款与监管解除引用当时资格。
        """
        project = self._project(project_id)
        if actor.role is not Role.BANK or actor.org_id != project.bank_id:
            raise PermissionDenied("资金事实须由主办银行确认")
        with project.lock:
            policy = self._policies.effective(as_of)
            eligibility = self._latest_eligibility_locked(project)
            stale = eligibility is not None and eligibility.project_version != project.facts.version
            if stale:
                eligibility = None
            release_approved = any(
                a.scope == SCOPE_SUPERVISION_RELEASE for a in project.approvals
            )
            verdict = adjudicate_fund_action(
                action,
                project.facts.view(),
                project.ledger,
                policy,
                eligibility,
                stale,
                release_approved,
                as_of,
            )
            adjudication = Adjudication(
                adjudication_id=self._next_id("A"),
                project_id=project_id,
                project_version=project.facts.version,
                subject=action.kind.value,
                outcome=verdict.outcome,
                policy_hits=verdict.policy_hits,
                materials=verdict.materials,
                quota_changes=verdict.quota_changes,
                intercepts=verdict.intercepts,
                references=verdict.references,
                requested_by=actor.actor_id,
                created_at=self._clock(),
            )
            project.adjudications.append(adjudication)
            if verdict.outcome is Outcome.ALLOW:
                entry = LedgerEntry(
                    entry_id=self._next_id("L"),
                    project_id=project_id,
                    kind=action.kind,
                    direction=direction_of(action.kind),
                    amount=action.amount,
                    adjudication_id=adjudication.adjudication_id,
                    confirmed_by=actor.actor_id,
                    project_version=project.facts.version,
                    created_at=self._clock(),
                    declared_total_price=(
                        action.declared_total_price
                        if action.kind is LedgerKind.EXISTING_DEPOSIT
                        else None
                    ),
                )
                project.ledger.append(entry)
            return adjudication

    # ---- 复核读取 ----

    def fact_version(self, project_id: str) -> int:
        project = self._project(project_id)
        with project.lock:
            return project.facts.version

    def adjudications(self, project_id: str) -> tuple[Adjudication, ...]:
        project = self._project(project_id)
        with project.lock:
            return tuple(project.adjudications)

    def adjudication(self, project_id: str, adjudication_id: str) -> Adjudication:
        project = self._project(project_id)
        with project.lock:
            for adjudication in project.adjudications:
                if adjudication.adjudication_id == adjudication_id:
                    return adjudication
        raise NotFound(f"裁定不存在：{adjudication_id}")

    def ledger_entries(self, project_id: str) -> tuple[LedgerEntry, ...]:
        project = self._project(project_id)
        with project.lock:
            return project.ledger.entries()

    def ledger_balance(self, project_id: str) -> Decimal:
        project = self._project(project_id)
        with project.lock:
            return project.ledger.balance()

    def entry_for_adjudication(self, project_id: str, adjudication_id: str) -> LedgerEntry | None:
        project = self._project(project_id)
        with project.lock:
            for entry in project.ledger.entries():
                if entry.adjudication_id == adjudication_id:
                    return entry
        return None
