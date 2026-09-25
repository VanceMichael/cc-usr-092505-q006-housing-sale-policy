"""测试共用的政策版本与服务构造。"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from housing_sale_policy.policy import ClassificationRule, PolicyBook, PolicyVersion
from housing_sale_policy.service import Actor, AdjudicationService, Role
from housing_sale_policy.temporal import FactKind

CUTOFF = date(2024, 6, 1)
DECISION_DAY = date(2026, 1, 10)
FUND_DAY = date(2026, 2, 1)

OLD_POLICY = PolicyVersion(
    policy_id="P2020",
    effective_from=date(2020, 1, 1),
    classification=(
        ClassificationRule("P2020-c1", FactKind.LAND_ANNOUNCEMENT, date(9999, 12, 31)),
    ),
    presale_requires_topping_out=False,
    presale_requires_supervision=False,
    deposit_cap_ratio=Decimal("0.20"),
    mortgage_supervised_contract_cutoff=None,
)

NEW_POLICY = PolicyVersion(
    policy_id="P2024",
    effective_from=CUTOFF,
    classification=(
        ClassificationRule("P2024-c1", FactKind.LAND_ANNOUNCEMENT, CUTOFF),
        ClassificationRule("P2024-c2", FactKind.PLANNING_PERMIT, CUTOFF),
    ),
    presale_requires_topping_out=True,
    presale_requires_supervision=True,
    deposit_cap_ratio=Decimal("0.20"),
    mortgage_supervised_contract_cutoff=CUTOFF,
)


def make_book() -> PolicyBook:
    return PolicyBook([OLD_POLICY, NEW_POLICY])


def make_service() -> AdjudicationService:
    """两个项目：P1 归 dev1、P2 归 dev2，主办银行均为 bank1。"""
    service = AdjudicationService(make_book())
    for actor in (
        Actor("dev1", Role.DEVELOPER),
        Actor("dev2", Role.DEVELOPER),
        Actor("bank1", Role.BANK),
        Actor("bank2", Role.BANK),
        Actor("staff1", Role.APPROVER),
        Actor("staff2", Role.APPROVER),
        Actor("reviewer", Role.REVIEWER),
    ):
        service.register_actor(actor)
    service.register_project(
        project_id="P1", name="在途项目", developer_id="dev1", bank_id="bank1"
    )
    service.register_project(
        project_id="P2", name="新项目", developer_id="dev2", bank_id="bank1"
    )
    return service


def submit(service, project_id, kind, occurred_on, actor="dev1", **kwargs):
    """以当前项目版本提交事实。"""
    return service.submit_fact(
        actor_id=actor,
        project_id=project_id,
        kind=kind,
        occurred_on=occurred_on,
        source="测试文号",
        expected_version=service.project_version(project_id),
        **kwargs,
    )


def adjudicate(service, project_id, actor="staff1", decided_on=DECISION_DAY, **kwargs):
    """以当前项目版本作出裁定。"""
    return service.adjudicate(
        actor_id=actor,
        project_id=project_id,
        expected_version=service.project_version(project_id),
        decided_on=decided_on,
        **kwargs,
    )


def confirm(service, project_id, kind, amount, actor="bank1", **kwargs):
    """以当前项目版本确认资金。"""
    return service.confirm_funds(
        actor_id=actor,
        project_id=project_id,
        kind=kind,
        amount=Decimal(str(amount)),
        occurred_on=FUND_DAY,
        expected_version=service.project_version(project_id),
        **kwargs,
    )
