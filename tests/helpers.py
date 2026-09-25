"""测试共享构造器。"""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from housing_sale_policy.facts import FactKind
from housing_sale_policy.policy import PolicyBook, PolicyVersion
from housing_sale_policy.service import Actor, AdjudicationService, Role

POLICY_FROM = date(2026, 1, 1)
AS_OF = date(2026, 9, 1)

DEVELOPER = Actor('u-dev-1', Role.DEVELOPER, 'DEV-1')
OTHER_DEVELOPER = Actor('u-dev-2', Role.DEVELOPER, 'DEV-2')
BANK = Actor('u-bank-1', Role.BANK, 'BANK-1')
OTHER_BANK = Actor('u-bank-9', Role.BANK, 'BANK-9')
OFFICER = Actor('u-off-1', Role.CASE_OFFICER, 'GOV')
OFFICER2 = Actor('u-off-2', Role.CASE_OFFICER, 'GOV')
REVIEWER = Actor('u-rev-1', Role.REVIEWER, 'GOV')

PROJECT_ID = 'P1'


def make_policy(**overrides) -> PolicyVersion:
    values = dict(
        policy_id='商品住房销售政策',
        version=1,
        effective_from=POLICY_FROM,
        deposit_cap_ratio=Decimal('0.2'),
    )
    values.update(overrides)
    return PolicyVersion(**values)


def make_service(*policies: PolicyVersion) -> AdjudicationService:
    book = PolicyBook()
    for policy in policies or (make_policy(),):
        book.register(policy)
    service = AdjudicationService(book)
    service.register_project(PROJECT_ID, '示例项目', 'DEV-1', 'BANK-1')
    return service


def seed_new_project(service: AdjudicationService, topping: bool = True) -> None:
    """录入一个新项目事实：土地公告日在政策生效日之后。"""
    service.submit_fact(
        PROJECT_ID, FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1), 'GG-2026-01', '自然资源局', DEVELOPER
    )
    if topping:
        service.submit_fact(
            PROJECT_ID, FactKind.STRUCTURE_TOPPING, date(2026, 8, 1), 'FD-2026-08', '施工单位', DEVELOPER
        )
