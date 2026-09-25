"""裁定服务：权限控制、项目版本并发、资金动作校验与复核解释。

- 开发企业只能提交本项目材料，资金事实由主办银行确认，审批人与材料提交人不得相同；
- 所有变更携带期望项目版本，补证与付款并发时基于同一项目版本串行提交；
- 被拒绝的事项不写入账本、不占用额度，但保留处理记录供复核；
- 定金上限、按揭放款与监管解除必须引用当时资格（最新裁定）；
- 市级复核可抽取任一项目和资金动作，逐项查看政策日期、材料、额度变化与拦截原因。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

from .ledger import FundKind, LedgerEntry, ProjectLedger
from .policy import PolicyBook
from .ruling import (
    MaterialRef,
    PolicyHit,
    Route,
    Ruling,
    adjudicate_project,
    seizure_active,
)
from .temporal import Fact, FactKind, FactLog


class Role(str, Enum):
    """参与方角色。"""

    DEVELOPER = "developer"  # 房地产开发企业
    BANK = "bank"  # 主办银行
    APPROVER = "approver"  # 住房管理受理/审批人员
    REVIEWER = "reviewer"  # 项目复核人员


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: Role


class VersionConflictError(RuntimeError):
    """项目版本已变化：并发操作须基于同一项目版本重新读取后提交。"""


@dataclass(frozen=True)
class QuotaChange:
    """一次动作引起的额度变化；被拒绝的动作前后相同。"""

    quota: str
    before: Decimal
    after: Decimal


@dataclass(frozen=True)
class DecisionRecord:
    """一次业务动作的处理记录（含被拒绝的动作）。"""

    decision_id: str
    project_id: str
    action: str
    actor_id: str
    allowed: bool
    project_version: int  # 处理时的项目版本
    result_id: str | None = None  # 产生的事实、账目或裁定编号
    policy_hits: tuple[PolicyHit, ...] = ()
    materials: tuple[MaterialRef, ...] = ()
    quota_changes: tuple[QuotaChange, ...] = ()
    intercepts: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """JSON 安全的逐项解释。"""
        return {
            "decision_id": self.decision_id,
            "project_id": self.project_id,
            "action": self.action,
            "actor_id": self.actor_id,
            "allowed": self.allowed,
            "project_version": self.project_version,
            "result_id": self.result_id,
            "policy_hits": [
                {
                    "rule_id": hit.rule_id,
                    "anchor": hit.anchor,
                    "cutoff": hit.cutoff.isoformat(),
                    "project_date": (
                        hit.project_date.isoformat() if hit.project_date else None
                    ),
                    "outcome": hit.outcome,
                }
                for hit in self.policy_hits
            ],
            "materials": [
                {
                    "fact_id": item.fact_id,
                    "kind": item.kind.value,
                    "occurred_on": item.occurred_on.isoformat(),
                    "source": item.source,
                    "submitted_by": item.submitted_by,
                }
                for item in self.materials
            ],
            "quota_changes": [
                {"quota": item.quota, "before": str(item.before), "after": str(item.after)}
                for item in self.quota_changes
            ],
            "intercepts": list(self.intercepts),
        }


@dataclass
class _Project:
    project_id: str
    name: str
    developer_id: str
    bank_id: str
    version: int = 0
    supervision_opened: bool = False
    facts: FactLog = None  # type: ignore[assignment]
    ledger: ProjectLedger = None  # type: ignore[assignment]
    rulings: list[Ruling] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.facts = FactLog()
        self.ledger = ProjectLedger(self.project_id)
        self.rulings = []


class AdjudicationService:
    """商品住房销售裁定服务。"""

    def __init__(self, policies: PolicyBook) -> None:
        self._policies = policies
        self._actors: dict[str, Actor] = {}
        self._projects: dict[str, _Project] = {}
        self._decisions: dict[str, DecisionRecord] = {}
        self._seq = 0

    # ---- 基础登记 ----

    def register_actor(self, actor: Actor) -> None:
        self._actors[actor.actor_id] = actor

    def register_project(
        self, *, project_id: str, name: str, developer_id: str, bank_id: str
    ) -> None:
        developer = self._actors.get(developer_id)
        bank = self._actors.get(bank_id)
        if developer is None or developer.role is not Role.DEVELOPER:
            raise ValueError("项目须登记在房地产开发企业名下")
        if bank is None or bank.role is not Role.BANK:
            raise ValueError("项目须指定主办银行")
        if project_id in self._projects:
            raise ValueError(f"项目编号重复: {project_id}")
        self._projects[project_id] = _Project(
            project_id=project_id, name=name, developer_id=developer_id, bank_id=bank_id
        )

    # ---- 内部工具 ----

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _project(self, project_id: str) -> _Project:
        try:
            return self._projects[project_id]
        except KeyError:
            raise KeyError(f"项目不存在: {project_id}") from None

    def _check_version(self, project: _Project, expected_version: int) -> None:
        if project.version != expected_version:
            raise VersionConflictError(
                f"项目 {project.project_id} 当前版本为 {project.version}，"
                f"与期望版本 {expected_version} 不一致，请重新读取后提交"
            )

    def _record(self, project: _Project, **kwargs) -> DecisionRecord:
        record = DecisionRecord(
            decision_id=f"D{self._next_seq():06d}",
            project_id=project.project_id,
            project_version=project.version,
            **kwargs,
        )
        self._decisions[record.decision_id] = record
        return record

    def _current_ruling(self, project: _Project) -> Ruling | None:
        return project.rulings[-1] if project.rulings else None

    # ---- 项目材料（时态事实） ----

    def submit_fact(
        self,
        *,
        actor_id: str,
        project_id: str,
        kind: FactKind,
        occurred_on: date,
        source: str,
        expected_version: int,
        corrects: str | None = None,
        detail: Mapping[str, str] | None = None,
    ) -> DecisionRecord:
        """开发企业提交本项目材料，形成带来源的时态事实。"""
        project = self._project(project_id)
        self._check_version(project, expected_version)
        intercepts: list[str] = []
        actor = self._actors.get(actor_id)
        if actor is None or actor.role not in (Role.DEVELOPER, Role.APPROVER):
            intercepts.append("仅开发企业或住房管理受理人员可提交项目材料")
        elif actor.role is Role.DEVELOPER and actor_id != project.developer_id:
            intercepts.append("开发企业只能提交本项目材料")
        if corrects is not None:
            try:
                project.facts.get(corrects)
            except KeyError:
                intercepts.append(f"被更正的事实不存在: {corrects}")
        if intercepts:
            return self._record(
                project, action="submit_fact", actor_id=actor_id,
                allowed=False, intercepts=tuple(intercepts),
            )
        seq = self._next_seq()
        fact = Fact(
            fact_id=f"F{seq:06d}",
            project_id=project_id,
            kind=kind,
            occurred_on=occurred_on,
            source=source,
            submitted_by=actor_id,
            seq=seq,
            corrects=corrects,
            detail=detail or {},
        )
        project.facts.append(fact)
        project.version += 1
        material = MaterialRef(
            fact.fact_id, fact.kind, fact.occurred_on, fact.source, fact.submitted_by
        )
        return self._record(
            project, action="submit_fact", actor_id=actor_id, allowed=True,
            result_id=fact.fact_id, materials=(material,),
        )

    def open_supervision(
        self, *, actor_id: str, project_id: str, expected_version: int
    ) -> DecisionRecord:
        """主办银行确认开立监管账户（资金全过程监管的前提）。"""
        project = self._project(project_id)
        self._check_version(project, expected_version)
        intercepts: list[str] = []
        actor = self._actors.get(actor_id)
        if actor is None or actor.role is not Role.BANK or actor_id != project.bank_id:
            intercepts.append("监管账户须由本项目主办银行确认开立")
        elif project.supervision_opened:
            intercepts.append("监管账户已开立")
        if intercepts:
            return self._record(
                project, action="open_supervision", actor_id=actor_id,
                allowed=False, intercepts=tuple(intercepts),
            )
        project.supervision_opened = True
        project.version += 1
        return self._record(
            project, action="open_supervision", actor_id=actor_id, allowed=True
        )

    # ---- 裁定 ----

    def adjudicate(
        self,
        *,
        actor_id: str,
        project_id: str,
        expected_version: int,
        decided_on: date,
        corrects: str | None = None,
    ) -> DecisionRecord:
        """审批人按有效政策版本裁定项目分路；更正只产生新裁定并保留旧结论。"""
        project = self._project(project_id)
        self._check_version(project, expected_version)
        policy = self._policies.effective_on(decided_on)
        route, classification, hits, materials, intercepts = adjudicate_project(
            log=project.facts,
            policy=policy,
            supervision_opened=project.supervision_opened,
        )
        procedural: list[str] = []
        actor = self._actors.get(actor_id)
        if actor is None or actor.role is not Role.APPROVER:
            procedural.append("仅住房管理审批人员可作出裁定")
        if actor_id in {item.submitted_by for item in materials}:
            procedural.append("审批人与材料提交人相同，违反职责分离")
        if corrects is not None:
            old = next((r for r in project.rulings if r.ruling_id == corrects), None)
            if old is None:
                procedural.append(f"被更正的裁定不存在: {corrects}")
            elif old is not self._current_ruling(project):
                procedural.append("被更正的裁定不是当前有效裁定")
        if procedural:
            return self._record(
                project, action="adjudicate", actor_id=actor_id, allowed=False,
                policy_hits=hits, materials=materials, intercepts=tuple(procedural),
            )
        seq = self._next_seq()
        ruling = Ruling(
            ruling_id=f"R{seq:06d}",
            project_id=project_id,
            policy_id=policy.policy_id,
            project_version=project.version,
            route=route,
            classification=classification,
            decided_on=decided_on,
            decided_by=actor_id,
            seq=seq,
            supersedes=corrects,
            policy_hits=hits,
            materials=materials,
            intercepts=intercepts,
        )
        project.rulings.append(ruling)
        project.version += 1
        return self._record(
            project, action="adjudicate", actor_id=actor_id, allowed=True,
            result_id=ruling.ruling_id, policy_hits=hits, materials=materials,
            intercepts=intercepts,
        )

    # ---- 资金动作 ----

    def confirm_funds(
        self,
        *,
        actor_id: str,
        project_id: str,
        kind: FundKind,
        amount: Decimal,
        occurred_on: date,
        expected_version: int,
        ruling_id: str | None = None,
        detail: Mapping[str, str] | None = None,
    ) -> DecisionRecord:
        """主办银行确认资金事实；定金、按揭与监管解除须引用当时资格。"""
        project = self._project(project_id)
        self._check_version(project, expected_version)
        detail = detail or {}
        intercepts: list[str] = []
        hits: tuple[PolicyHit, ...] = ()
        materials: tuple[MaterialRef, ...] = ()
        quota_changes: list[QuotaChange] = []

        actor = self._actors.get(actor_id)
        if actor is None or actor.role is not Role.BANK or actor_id != project.bank_id:
            intercepts.append("资金事实须由本项目主办银行确认")
        if kind is FundKind.REVERSAL:
            intercepts.append("冲正须通过 reverse_entry 办理")
        if amount <= 0:
            intercepts.append("金额必须为正")

        # 引用当时资格：必须是最新裁定，其政策参数即当时资格
        ruling: Ruling | None = None
        needs_ruling = kind in (
            FundKind.PRESALE_PROCEEDS,
            FundKind.EXISTING_HOME_DEPOSIT,
            FundKind.MORTGAGE_DISBURSEMENT,
            FundKind.SUPERVISION_RELEASE,
        )
        if needs_ruling:
            if ruling_id is None:
                intercepts.append("该资金动作必须引用当时资格（最新裁定）")
            else:
                ruling = next(
                    (r for r in project.rulings if r.ruling_id == ruling_id), None
                )
                if ruling is None:
                    intercepts.append(f"引用的裁定不存在: {ruling_id}")
                elif ruling is not self._current_ruling(project):
                    intercepts.append("资格已更正，须引用最新裁定")
                else:
                    hits = ruling.policy_hits
                    materials = ruling.materials
        policy = (
            self._policies.get(ruling.policy_id) if ruling is not None else None
        )

        supervised_before = project.ledger.supervised_balance()
        released = project.ledger.supervision_released()

        if kind is FundKind.PRESALE_PROCEEDS:
            if ruling is not None and ruling.route is not Route.PRESALE:
                intercepts.append("当前资格不允许预售，预售款不得入账")
            if released:
                intercepts.append("监管已解除，预售款不得再入监管账户")
            quota_changes.append(
                QuotaChange("supervised_balance", supervised_before, supervised_before + amount)
            )
        elif kind is FundKind.MORTGAGE_DISBURSEMENT:
            if ruling is not None and ruling.route is not Route.PRESALE:
                intercepts.append("当前资格不允许预售，按揭不得放款")
            if released:
                intercepts.append("监管已解除，按揭放款不得再入监管账户")
            cutoff = policy.mortgage_supervised_contract_cutoff if policy else None
            if cutoff is not None:
                raw = detail.get("loan_contract_date")
                contract_date = _parse_date(raw)
                if contract_date is None:
                    intercepts.append("缺少或无法识别贷款合同日期")
                else:
                    outcome = (
                        "贷款合同日不早于政策基准日，按揭须入监管账户"
                        if contract_date >= cutoff
                        else "贷款合同日早于政策基准日"
                    )
                    hits = hits + (
                        PolicyHit(
                            f"{policy.policy_id}:mortgage-supervised",
                            "loan_contract_date",
                            cutoff,
                            contract_date,
                            outcome,
                        ),
                    )
                    if contract_date >= cutoff and detail.get("to_supervised_account") != "true":
                        intercepts.append("按揭放款须进入监管账户")
            quota_changes.append(
                QuotaChange("supervised_balance", supervised_before, supervised_before + amount)
            )
        elif kind is FundKind.EXISTING_HOME_DEPOSIT:
            if ruling is not None and ruling.route is not Route.EXISTING_HOME_FILING:
                intercepts.append("当前资格不是现房备案，不得收取现房定金")
            if policy is not None:
                price = _parse_decimal(detail.get("house_price"))
                if price is None:
                    intercepts.append("缺少或无法识别房屋价款，无法校核定金上限")
                else:
                    cap = (policy.deposit_cap_ratio * price).quantize(Decimal("0.01"))
                    if amount > cap:
                        intercepts.append(
                            f"现房定金 {amount} 超过当时资格规定的上限 {cap}"
                        )
                    remaining = cap - amount if amount <= cap else cap
                    quota_changes.append(
                        QuotaChange("deposit_allowance", cap, remaining)
                    )
        elif kind is FundKind.ENTRUSTED_PAYMENT:
            if released:
                intercepts.append("监管已解除，不得再受托支付")
            if amount > supervised_before:
                intercepts.append(
                    f"受托支付 {amount} 超出监管账户可用额度 {supervised_before}"
                )
            quota_changes.append(
                QuotaChange("supervised_balance", supervised_before, supervised_before - amount)
            )
        elif kind is FundKind.SUPERVISION_RELEASE:
            joint = project.facts.latest(FactKind.JOINT_ACCEPTANCE)
            if joint is None:
                intercepts.append("联合验收前不得解除预售资金监管")
            else:
                materials = materials + (
                    MaterialRef(
                        joint.fact_id, joint.kind, joint.occurred_on,
                        joint.source, joint.submitted_by,
                    ),
                )
            if seizure_active(project.facts) is not None:
                intercepts.append("存在未解除的抵押查封，不得解除监管")
            if released:
                intercepts.append("监管已解除，不得重复解除")
            if amount != supervised_before:
                intercepts.append(
                    f"监管解除须一次性释放全部监管余额 {supervised_before}"
                )
            quota_changes.append(
                QuotaChange("supervised_balance", supervised_before, Decimal(0))
            )

        if intercepts:
            # 拒绝事项不占用额度：额度前后相同
            quota_changes = [
                QuotaChange(item.quota, item.before, item.before)
                for item in quota_changes
            ]
            return self._record(
                project, action=f"confirm_funds:{kind.value}", actor_id=actor_id,
                allowed=False, policy_hits=hits, materials=materials,
                quota_changes=tuple(quota_changes), intercepts=tuple(intercepts),
            )
        seq = self._next_seq()
        entry = LedgerEntry(
            entry_id=f"E{seq:06d}",
            project_id=project_id,
            kind=kind,
            amount=amount,
            occurred_on=occurred_on,
            confirmed_by=actor_id,
            seq=seq,
            ruling_id=ruling_id,
            detail=detail,
        )
        project.ledger.append(entry)
        project.version += 1
        return self._record(
            project, action=f"confirm_funds:{kind.value}", actor_id=actor_id,
            allowed=True, result_id=entry.entry_id, policy_hits=hits,
            materials=materials, quota_changes=tuple(quota_changes),
        )

    def reverse_entry(
        self,
        *,
        actor_id: str,
        project_id: str,
        entry_id: str,
        expected_version: int,
        reason: str,
    ) -> DecisionRecord:
        """冲正账目：账本不可改写，更正以冲正条目完成并保留原条目。"""
        project = self._project(project_id)
        self._check_version(project, expected_version)
        intercepts: list[str] = []
        actor = self._actors.get(actor_id)
        if actor is None or actor.role is not Role.BANK or actor_id != project.bank_id:
            intercepts.append("冲正须由本项目主办银行确认")
        target: LedgerEntry | None = None
        try:
            target = project.ledger.get(entry_id)
            if target.kind is FundKind.REVERSAL:
                intercepts.append("冲正条目不得再被冲正")
            elif project.ledger.is_reversed(entry_id):
                intercepts.append(f"条目已被冲正: {entry_id}")
        except KeyError:
            intercepts.append(f"被冲正条目不存在: {entry_id}")
        before = project.ledger.supervised_balance()
        after = before
        if target is not None and not intercepts:
            after = before - project.ledger.supervised_effect(target)
            if after < 0:
                intercepts.append("冲正将导致监管账户余额为负")
        if intercepts:
            return self._record(
                project, action="reverse_entry", actor_id=actor_id, allowed=False,
                quota_changes=(QuotaChange("supervised_balance", before, before),),
                intercepts=tuple(intercepts),
            )
        seq = self._next_seq()
        entry = LedgerEntry(
            entry_id=f"E{seq:06d}",
            project_id=project_id,
            kind=FundKind.REVERSAL,
            amount=target.amount,
            occurred_on=target.occurred_on,
            confirmed_by=actor_id,
            seq=seq,
            reverses=target.entry_id,
            detail={"reason": reason},
        )
        project.ledger.append(entry)
        project.version += 1
        return self._record(
            project, action="reverse_entry", actor_id=actor_id, allowed=True,
            result_id=entry.entry_id,
            quota_changes=(QuotaChange("supervised_balance", before, after),),
        )

    # ---- 市级复核 ----

    def review_decision(self, decision_id: str) -> dict:
        """逐项列出单个动作命中的政策日期、所用材料、额度变化与拦截原因。"""
        return self._decisions[decision_id].to_dict()

    def review_project(self, project_id: str) -> dict:
        """项目复核总览：当前资格、监管余额与全部处理记录。"""
        project = self._project(project_id)
        ruling = self._current_ruling(project)
        return {
            "project_id": project.project_id,
            "name": project.name,
            "version": project.version,
            "supervision_opened": project.supervision_opened,
            "supervised_balance": str(project.ledger.supervised_balance()),
            "supervision_released": project.ledger.supervision_released(),
            "current_ruling": (
                {
                    "ruling_id": ruling.ruling_id,
                    "policy_id": ruling.policy_id,
                    "route": ruling.route.value,
                    "classification": ruling.classification,
                    "supersedes": ruling.supersedes,
                }
                if ruling
                else None
            ),
            "rulings": [r.ruling_id for r in project.rulings],
            "decisions": [
                d.decision_id
                for d in self._decisions.values()
                if d.project_id == project_id
            ],
        }

    # ---- 查询 ----

    def current_ruling(self, project_id: str) -> Ruling | None:
        return self._current_ruling(self._project(project_id))

    def get_ruling(self, project_id: str, ruling_id: str) -> Ruling:
        project = self._project(project_id)
        for ruling in project.rulings:
            if ruling.ruling_id == ruling_id:
                return ruling
        raise KeyError(ruling_id)

    def supervised_balance(self, project_id: str) -> Decimal:
        return self._project(project_id).ledger.supervised_balance()

    def project_version(self, project_id: str) -> int:
        return self._project(project_id).version


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _parse_decimal(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None
