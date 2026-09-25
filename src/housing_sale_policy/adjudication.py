"""裁定记录与资金动作裁定规则。

每次裁定都产生一条不可改写的裁定记录：结论、命中的政策日期、所用
材料、额度变化与拦截原因一并保存，供市级复核逐项核对。规则更正只能
产生新裁定并保留旧结论。资金动作必须引用当时有效的资格裁定；项目
事实补证后，原资格裁定不再适用，须重新裁定。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from .facts import FactKind, FactView, MaterialRef
from .ledger import OUT_KINDS, LedgerKind, ProjectLedger
from .policy import Eligibility, PolicyHit, PolicyVersion

SUBJECT_ELIGIBILITY = "资格裁定"


class Outcome(Enum):
    """裁定结论：资格结论或资金动作的允许/拒绝。"""

    ALLOW = "允许"
    DENY = "拒绝"
    PRESALE = "可申请预售"
    EXISTING_HOME = "现房备案"
    DISTRICT_REVIEW = "需区政府评估和市级备案"
    NOT_ELIGIBLE = "暂不具备条件"


ELIGIBILITY_TO_OUTCOME = {
    Eligibility.PRESALE: Outcome.PRESALE,
    Eligibility.EXISTING_HOME: Outcome.EXISTING_HOME,
    Eligibility.DISTRICT_REVIEW: Outcome.DISTRICT_REVIEW,
    Eligibility.NOT_ELIGIBLE: Outcome.NOT_ELIGIBLE,
}
OUTCOME_TO_ELIGIBILITY = {outcome: eligibility for eligibility, outcome in ELIGIBILITY_TO_OUTCOME.items()}


@dataclass(frozen=True)
class QuotaChange:
    """一次允许动作造成的额度变化。"""

    account: str
    before: Decimal
    after: Decimal


@dataclass(frozen=True)
class FundAction:
    """资金动作请求；现房定金须声明房屋总价以校验定金上限。"""

    kind: LedgerKind
    amount: Decimal
    declared_total_price: Decimal | None = None
    note: str = ""


@dataclass(frozen=True)
class Adjudication:
    """一条裁定记录，一经产生不可改写；更正只能以新裁定取代。"""

    adjudication_id: str
    project_id: str
    project_version: int
    subject: str
    outcome: Outcome
    policy_hits: tuple[PolicyHit, ...]
    materials: tuple[MaterialRef, ...]
    quota_changes: tuple[QuotaChange, ...]
    intercepts: tuple[str, ...]
    references: tuple[str, ...]
    requested_by: str
    created_at: datetime
    supersedes: str | None = None


@dataclass(frozen=True)
class ActionVerdict:
    """资金动作裁定结果，由服务层组装为裁定记录。"""

    outcome: Outcome
    intercepts: tuple[str, ...]
    quota_changes: tuple[QuotaChange, ...]
    materials: tuple[MaterialRef, ...]
    policy_hits: tuple[PolicyHit, ...]
    references: tuple[str, ...]


def adjudicate_fund_action(
    action: FundAction,
    view: FactView,
    ledger: ProjectLedger,
    policy: PolicyVersion,
    eligibility: Adjudication | None,
    eligibility_stale: bool,
    release_approved: bool,
    as_of: date,
) -> ActionVerdict:
    """按当时资格与有效政策版本裁定资金动作。

    定金上限、按揭放款与监管解除必须引用当时资格；拒绝事项不产生
    任何额度变化。返回结论与全部解释要素。
    """
    intercepts: list[str] = []
    materials: list[MaterialRef] = []
    references: list[str] = []
    hits: list[PolicyHit] = [
        PolicyHit(policy.policy_id, policy.version, policy.effective_from, None, ())
    ]

    if action.amount <= 0:
        intercepts.append("金额必须为正")

    def require_eligibility(expected: tuple[Eligibility, ...], label: str) -> None:
        if eligibility is None:
            if eligibility_stale:
                intercepts.append(f"项目事实已补证，原资格裁定不再适用，重新裁定后方可办理{label}")
            else:
                intercepts.append(f"缺少有效资格裁定，不能办理{label}")
            return
        current = OUTCOME_TO_ELIGIBILITY.get(eligibility.outcome)
        if current not in expected:
            wanted = "、".join(e.value for e in expected)
            intercepts.append(f"当前资格为「{eligibility.outcome.value}」，需「{wanted}」方可办理{label}")
            return
        hits.extend(eligibility.policy_hits)
        references.append(eligibility.adjudication_id)

    kind = action.kind
    if kind in OUT_KINDS:
        seizure = view.latest(FactKind.MORTGAGE_SEIZURE)
        if seizure is not None and seizure.occurred_on <= as_of:
            intercepts.append(f"存在抵押查封（{seizure.occurred_on.isoformat()}），禁止资金出账")
            materials.append(MaterialRef.of(seizure))

    balance = ledger.balance()
    cum_deposit = ledger.total(LedgerKind.EXISTING_DEPOSIT)
    cum_price = ledger.total_declared_price()
    new_deposit = cum_deposit + action.amount
    new_price = cum_price + (action.declared_total_price or Decimal("0"))
    deposit_cap = policy.deposit_cap_ratio * new_price

    if kind in (LedgerKind.PRESALE_FUNDS, LedgerKind.MORTGAGE_DISBURSEMENT):
        require_eligibility((Eligibility.PRESALE,), kind.value)
    elif kind is LedgerKind.EXISTING_DEPOSIT:
        require_eligibility((Eligibility.EXISTING_HOME,), kind.value)
        if action.declared_total_price is None or action.declared_total_price <= 0:
            intercepts.append("现房定金须声明房屋总价以校验定金上限")
        elif new_deposit > deposit_cap:
            intercepts.append(
                f"现房定金累计{new_deposit}超过定金上限{deposit_cap}"
                f"（房屋总价{new_price}×比例{policy.deposit_cap_ratio}）"
            )
    elif kind is LedgerKind.ENTRUSTED_PAYMENT:
        if action.amount > balance:
            intercepts.append(f"受托支付{action.amount}超过监管账户余额{balance}")
    elif kind is LedgerKind.SUPERVISION_RELEASE:
        require_eligibility((Eligibility.PRESALE, Eligibility.EXISTING_HOME), kind.value)
        joint = view.latest(FactKind.JOINT_ACCEPTANCE)
        if policy.release_requires_joint_acceptance and (joint is None or joint.occurred_on > as_of):
            intercepts.append("联合验收前不得解除预售资金监管")
        elif joint is not None:
            materials.append(MaterialRef.of(joint))
        if not release_approved:
            intercepts.append("监管解除未获住房管理受理人员审批")
        if action.amount > balance:
            intercepts.append(f"解除金额{action.amount}超过监管账户余额{balance}")

    quota_changes: list[QuotaChange] = []
    if not intercepts:
        if kind in OUT_KINDS:
            quota_changes.append(QuotaChange("监管账户", balance, balance - action.amount))
        else:
            quota_changes.append(QuotaChange("监管账户", balance, balance + action.amount))
        if kind is LedgerKind.EXISTING_DEPOSIT:
            quota_changes.append(QuotaChange("定金累计", cum_deposit, new_deposit))
            quota_changes.append(
                QuotaChange("定金上限", policy.deposit_cap_ratio * cum_price, deposit_cap)
            )

    outcome = Outcome.DENY if intercepts else Outcome.ALLOW
    return ActionVerdict(
        outcome,
        tuple(intercepts),
        tuple(quota_changes),
        tuple(materials),
        tuple(hits),
        tuple(references),
    )
