import threading
import unittest
from datetime import date
from decimal import Decimal

from helpers import (
    AS_OF,
    BANK,
    DEVELOPER,
    OFFICER,
    OFFICER2,
    OTHER_BANK,
    OTHER_DEVELOPER,
    PROJECT_ID,
    REVIEWER,
    make_policy,
    make_service,
    seed_new_project,
)
from housing_sale_policy.adjudication import FundAction, Outcome
from housing_sale_policy.facts import FactKind
from housing_sale_policy.ledger import LedgerKind
from housing_sale_policy.service import PermissionDenied, StateError


class AccessControlTest(unittest.TestCase):
    def test_developer_can_only_submit_own_project(self) -> None:
        service = make_service()
        with self.assertRaises(PermissionDenied):
            service.submit_fact(
                PROJECT_ID, FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1),
                'GG-1', '自然资源局', OTHER_DEVELOPER,
            )

    def test_officer_can_submit_fact(self) -> None:
        service = make_service()
        fact_id = service.submit_fact(
            PROJECT_ID, FactKind.PLANNING_PERMIT, date(2026, 4, 1), 'GH-1', '规划局', OFFICER
        )
        self.assertTrue(fact_id.startswith('F-'))

    def test_reviewer_cannot_submit_fact(self) -> None:
        service = make_service()
        with self.assertRaises(PermissionDenied):
            service.submit_fact(
                PROJECT_ID, FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1),
                'GG-1', '自然资源局', REVIEWER,
            )

    def test_fund_action_requires_sponsor_bank(self) -> None:
        service = make_service()
        action = FundAction(LedgerKind.DEVELOP_LOAN, Decimal('100'))
        with self.assertRaises(PermissionDenied):
            service.request_fund_action(PROJECT_ID, action, DEVELOPER, AS_OF)
        with self.assertRaises(PermissionDenied):
            service.request_fund_action(PROJECT_ID, action, OTHER_BANK, AS_OF)


class PresaleFlowTest(unittest.TestCase):
    def test_full_presale_flow(self) -> None:
        service = make_service()
        seed_new_project(service)
        eligibility = service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)
        self.assertIs(eligibility.outcome, Outcome.PRESALE)

        loan = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.DEVELOP_LOAN, Decimal('1000')), BANK, AS_OF
        )
        self.assertIs(loan.outcome, Outcome.ALLOW)

        presale = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.PRESALE_FUNDS, Decimal('500')), BANK, AS_OF
        )
        self.assertIs(presale.outcome, Outcome.ALLOW)
        # 预售款入账引用当时资格裁定
        self.assertIn(eligibility.adjudication_id, presale.references)

        payment = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.ENTRUSTED_PAYMENT, Decimal('200')), BANK, AS_OF
        )
        self.assertIs(payment.outcome, Outcome.ALLOW)
        self.assertEqual(service.ledger_balance(PROJECT_ID), Decimal('1300'))

        # 账本分录引用产生它的裁定，可追溯到当时资格
        entries = service.ledger_entries(PROJECT_ID)
        self.assertEqual(len(entries), 3)
        by_id = {a.adjudication_id: a for a in service.adjudications(PROJECT_ID)}
        for entry in entries:
            self.assertIn(entry.adjudication_id, by_id)
        self.assertEqual(entries[-1].confirmed_by, BANK.actor_id)

    def test_in_transition_project_cannot_take_presale_funds(self) -> None:
        service = make_service()
        service.submit_fact(
            PROJECT_ID, FactKind.LAND_ANNOUNCEMENT, date(2025, 6, 1), 'GG-1', '自然资源局', DEVELOPER
        )
        service.submit_fact(
            PROJECT_ID, FactKind.STRUCTURE_TOPPING, date(2026, 8, 1), 'FD-1', '施工单位', DEVELOPER
        )
        eligibility = service.assess_eligibility(PROJECT_ID, OFFICER, AS_OF)
        self.assertIs(eligibility.outcome, Outcome.DISTRICT_REVIEW)

        presale = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.PRESALE_FUNDS, Decimal('500')), BANK, AS_OF
        )
        self.assertIs(presale.outcome, Outcome.DENY)
        self.assertTrue(any('需区政府评估和市级备案' in i for i in presale.intercepts))
        # 拒绝事项不占用额度
        self.assertEqual(service.ledger_balance(PROJECT_ID), Decimal('0'))
        self.assertEqual(service.ledger_entries(PROJECT_ID), ())

    def test_evidence_stales_eligibility_until_reassessed(self) -> None:
        service = make_service()
        seed_new_project(service)
        service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)

        # 补证后项目版本前进，原资格裁定不再适用
        service.submit_fact(
            PROJECT_ID, FactKind.PARTIAL_ACCEPTANCE, date(2026, 8, 20), 'FB-1', '质监站', OFFICER
        )
        mortgage = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.MORTGAGE_DISBURSEMENT, Decimal('300')), BANK, AS_OF
        )
        self.assertIs(mortgage.outcome, Outcome.DENY)
        self.assertTrue(any('补证' in i for i in mortgage.intercepts))

        service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)
        mortgage = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.MORTGAGE_DISBURSEMENT, Decimal('300')), BANK, AS_OF
        )
        self.assertIs(mortgage.outcome, Outcome.ALLOW)


class SupervisionReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()
        seed_new_project(self.service)
        self.service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)
        self.service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.PRESALE_FUNDS, Decimal('500')), BANK, AS_OF
        )

    def test_release_before_joint_acceptance_is_blocked(self) -> None:
        release = self.service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.SUPERVISION_RELEASE, Decimal('100')), BANK, AS_OF
        )
        self.assertIs(release.outcome, Outcome.DENY)
        self.assertTrue(any('联合验收前不得解除' in i for i in release.intercepts))
        self.assertEqual(self.service.ledger_balance(PROJECT_ID), Decimal('500'))

    def test_release_flow_with_approval_and_separation(self) -> None:
        service = self.service
        # 受理人员 OFFICER 录入联合验收事实后，项目转入现房备案
        service.submit_fact(
            PROJECT_ID, FactKind.JOINT_ACCEPTANCE, date(2026, 8, 20), 'LH-1', '住建委', OFFICER
        )
        eligibility = service.assess_eligibility(PROJECT_ID, OFFICER2, AS_OF)
        self.assertIs(eligibility.outcome, Outcome.EXISTING_HOME)

        # 审批人与材料提交人不得相同：OFFICER 提交过材料，不能审批
        with self.assertRaises(PermissionDenied):
            service.approve_supervision_release(PROJECT_ID, OFFICER)
        service.approve_supervision_release(PROJECT_ID, OFFICER2)
        with self.assertRaises(StateError):
            service.approve_supervision_release(PROJECT_ID, OFFICER2)

        release = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.SUPERVISION_RELEASE, Decimal('100')), BANK, AS_OF
        )
        self.assertIs(release.outcome, Outcome.ALLOW)
        self.assertEqual(service.ledger_balance(PROJECT_ID), Decimal('400'))

    def test_release_without_approval_is_denied(self) -> None:
        service = self.service
        service.submit_fact(
            PROJECT_ID, FactKind.JOINT_ACCEPTANCE, date(2026, 8, 20), 'LH-1', '住建委', OFFICER
        )
        service.assess_eligibility(PROJECT_ID, OFFICER2, AS_OF)
        release = service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.SUPERVISION_RELEASE, Decimal('100')), BANK, AS_OF
        )
        self.assertIs(release.outcome, Outcome.DENY)
        self.assertTrue(any('审批' in i for i in release.intercepts))


class RuleCorrectionTest(unittest.TestCase):
    def test_correction_creates_new_adjudication_and_keeps_old(self) -> None:
        # 政策生效日误录为 2026-01-01，项目被分流为在途项目
        service = make_service(make_policy())
        service.submit_fact(
            PROJECT_ID, FactKind.LAND_ANNOUNCEMENT, date(2025, 6, 1), 'GG-1', '自然资源局', DEVELOPER
        )
        service.submit_fact(
            PROJECT_ID, FactKind.STRUCTURE_TOPPING, date(2026, 3, 1), 'FD-1', '施工单位', DEVELOPER
        )
        old = service.assess_eligibility(PROJECT_ID, OFFICER, AS_OF)
        self.assertIs(old.outcome, Outcome.DISTRICT_REVIEW)

        # 规则更正：登记生效日为 2025-01-01 的更正版本，项目实为新项目
        service.register_policy(
            make_policy(version=2, effective_from=date(2025, 1, 1), corrects=1)
        )
        new = service.correct_eligibility(PROJECT_ID, OFFICER, AS_OF)
        self.assertIs(new.outcome, Outcome.PRESALE)
        self.assertEqual(new.supersedes, old.adjudication_id)

        # 旧结论保留可查，当前资格以新裁定为准
        kept = service.adjudication(PROJECT_ID, old.adjudication_id)
        self.assertIs(kept.outcome, Outcome.DISTRICT_REVIEW)
        self.assertIs(service.current_eligibility(PROJECT_ID).adjudication_id, new.adjudication_id)

    def test_correction_requires_officer(self) -> None:
        service = make_service()
        seed_new_project(service)
        service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)
        with self.assertRaises(PermissionDenied):
            service.correct_eligibility(PROJECT_ID, DEVELOPER, AS_OF)


class DepositQuotaTest(unittest.TestCase):
    def test_rejected_deposit_consumes_no_quota(self) -> None:
        service = make_service()
        seed_new_project(service)
        service.submit_fact(
            PROJECT_ID, FactKind.JOINT_ACCEPTANCE, date(2026, 8, 20), 'LH-1', '住建委', OFFICER
        )
        service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)

        # 总价 100，定金上限 20%；30 超限被拒绝
        over = service.request_fund_action(
            PROJECT_ID,
            FundAction(LedgerKind.EXISTING_DEPOSIT, Decimal('30'), declared_total_price=Decimal('100')),
            BANK, AS_OF,
        )
        self.assertIs(over.outcome, Outcome.DENY)
        self.assertTrue(any('定金上限' in i for i in over.intercepts))
        self.assertEqual(service.ledger_balance(PROJECT_ID), Decimal('0'))

        # 拒绝未占用额度，20 仍可入账
        ok = service.request_fund_action(
            PROJECT_ID,
            FundAction(LedgerKind.EXISTING_DEPOSIT, Decimal('20'), declared_total_price=Decimal('100')),
            BANK, AS_OF,
        )
        self.assertIs(ok.outcome, Outcome.ALLOW)
        self.assertEqual(service.ledger_balance(PROJECT_ID), Decimal('20'))


class ConcurrencyTest(unittest.TestCase):
    def test_payment_and_evidence_concurrent_use_consistent_version(self) -> None:
        service = make_service()
        seed_new_project(service)
        service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)
        service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.DEVELOP_LOAN, Decimal('1050')), BANK, AS_OF
        )

        payments, payers, supplementers = [], 12, 4
        barrier = threading.Barrier(payers + supplementers)
        lock = threading.Lock()

        def pay() -> None:
            barrier.wait()
            adjudication = service.request_fund_action(
                PROJECT_ID, FundAction(LedgerKind.ENTRUSTED_PAYMENT, Decimal('100')), BANK, AS_OF
            )
            with lock:
                payments.append(adjudication)

        def supplement(index: int) -> None:
            barrier.wait()
            service.submit_fact(
                PROJECT_ID, FactKind.PARTIAL_ACCEPTANCE,
                date(2026, 8, 10 + index), f'FB-{index}', '质监站', OFFICER,
            )

        threads = [threading.Thread(target=pay) for _ in range(payers)]
        threads += [threading.Thread(target=supplement, args=(i,)) for i in range(supplementers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        allowed = [a for a in payments if a.outcome is Outcome.ALLOW]
        denied = [a for a in payments if a.outcome is Outcome.DENY]
        self.assertEqual(len(allowed), 10)
        self.assertEqual(len(denied), 2)
        for adjudication in denied:
            self.assertTrue(any('余额' in i for i in adjudication.intercepts))

        # 拒绝事项不占用额度：账本只含开发贷与 10 笔受托支付
        entries = service.ledger_entries(PROJECT_ID)
        self.assertEqual(len(entries), 11)
        self.assertEqual(service.ledger_balance(PROJECT_ID), Decimal('50'))

        # 每笔支付都在同一项目版本上完整裁定：额度变化首尾相接
        by_id = {a.adjudication_id: a for a in payments}
        expected = Decimal('1050')
        for entry in entries[1:]:
            changes = {
                c.account: c for c in by_id[entry.adjudication_id].quota_changes
            }
            self.assertEqual(changes['监管账户'].before, expected)
            self.assertEqual(changes['监管账户'].after, expected - Decimal('100'))
            expected = changes['监管账户'].after

        # 补证全部落账，事实版本单调递增
        self.assertEqual(service.fact_version(PROJECT_ID), 2 + supplementers)


if __name__ == '__main__':
    unittest.main()
