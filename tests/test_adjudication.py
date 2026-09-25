import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from housing_sale_policy.adjudication import (
    SUBJECT_ELIGIBILITY,
    Adjudication,
    FundAction,
    Outcome,
    adjudicate_fund_action,
)
from housing_sale_policy.facts import EvidenceSource, FactKind, FactLog, TemporalFact
from housing_sale_policy.ledger import LedgerEntry, LedgerKind, ProjectLedger, direction_of
from housing_sale_policy.policy import PolicyHit, PolicyVersion, ProjectClass

AS_OF = date(2026, 9, 1)
POLICY = PolicyVersion(
    policy_id='商品住房销售政策',
    version=1,
    effective_from=date(2026, 1, 1),
    deposit_cap_ratio=Decimal('0.2'),
)


def eligibility_adjudication(outcome: Outcome = Outcome.PRESALE) -> Adjudication:
    hit = PolicyHit('商品住房销售政策', 1, date(2026, 1, 1), ProjectClass.NEW, ())
    return Adjudication(
        adjudication_id='A-ELI-1',
        project_id='P1',
        project_version=2,
        subject=SUBJECT_ELIGIBILITY,
        outcome=outcome,
        policy_hits=(hit,),
        materials=(),
        quota_changes=(),
        intercepts=(),
        references=(),
        requested_by='u-dev-1',
        created_at=datetime(2026, 9, 1),
    )


def empty_view() -> FactLog:
    return FactLog().view()


def view_with(*facts: tuple[FactKind, date]):
    log = FactLog()
    for index, (kind, occurred_on) in enumerate(facts, start=1):
        log.append(
            TemporalFact(
                fact_id=f'F-{index}',
                kind=kind,
                occurred_on=occurred_on,
                source=EvidenceSource(f'DOC-{index}', '出具机构', 'u-dev-1', '开发企业'),
                recorded_at=datetime(2026, 9, 1),
            )
        )
    return log.view()


def ledger_with(*entries: tuple[LedgerKind, str]) -> ProjectLedger:
    ledger = ProjectLedger()
    for index, (kind, amount) in enumerate(entries, start=1):
        ledger.append(
            LedgerEntry(
                entry_id=f'L-{index}',
                project_id='P1',
                kind=kind,
                direction=direction_of(kind),
                amount=Decimal(amount),
                adjudication_id='A-0',
                confirmed_by='u-bank-1',
                project_version=1,
                created_at=datetime(2026, 9, 1),
            )
        )
    return ledger


def adjudicate(action, view, ledger, eligibility=None, stale=False, approved=False):
    return adjudicate_fund_action(
        action, view, ledger, POLICY, eligibility, stale, approved, AS_OF
    )


class FundActionAdjudicationTest(unittest.TestCase):
    def test_presale_funds_require_presale_eligibility(self) -> None:
        action = FundAction(LedgerKind.PRESALE_FUNDS, Decimal('100'))
        verdict = adjudicate(action, empty_view(), ProjectLedger(), eligibility_adjudication())
        self.assertIs(verdict.outcome, Outcome.ALLOW)
        self.assertIn('A-ELI-1', verdict.references)

        verdict = adjudicate(
            action, empty_view(), ProjectLedger(),
            eligibility_adjudication(Outcome.DISTRICT_REVIEW),
        )
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('需区政府评估和市级备案' in i for i in verdict.intercepts))

    def test_missing_eligibility_blocks_mortgage_disbursement(self) -> None:
        action = FundAction(LedgerKind.MORTGAGE_DISBURSEMENT, Decimal('100'))
        verdict = adjudicate(action, empty_view(), ProjectLedger(), None)
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('缺少有效资格裁定' in i for i in verdict.intercepts))

    def test_stale_eligibility_requires_new_adjudication(self) -> None:
        action = FundAction(LedgerKind.MORTGAGE_DISBURSEMENT, Decimal('100'))
        verdict = adjudicate(action, empty_view(), ProjectLedger(), None, stale=True)
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('补证' in i for i in verdict.intercepts))

    def test_deposit_cap_allows_within_limit(self) -> None:
        ledger = ProjectLedger()
        ledger.append(
            LedgerEntry(
                entry_id='L-1', project_id='P1', kind=LedgerKind.EXISTING_DEPOSIT,
                direction=direction_of(LedgerKind.EXISTING_DEPOSIT), amount=Decimal('20'),
                adjudication_id='A-0', confirmed_by='u-bank-1', project_version=1,
                created_at=datetime(2026, 9, 1), declared_total_price=Decimal('100'),
            )
        )
        action = FundAction(LedgerKind.EXISTING_DEPOSIT, Decimal('15'), declared_total_price=Decimal('100'))
        verdict = adjudicate(
            action, empty_view(), ledger, eligibility_adjudication(Outcome.EXISTING_HOME)
        )
        self.assertIs(verdict.outcome, Outcome.ALLOW)
        accounts = [change.account for change in verdict.quota_changes]
        self.assertEqual(accounts, ['监管账户', '定金累计', '定金上限'])

    def test_deposit_over_cap_is_denied_without_quota_change(self) -> None:
        ledger = ProjectLedger()
        ledger.append(
            LedgerEntry(
                entry_id='L-1', project_id='P1', kind=LedgerKind.EXISTING_DEPOSIT,
                direction=direction_of(LedgerKind.EXISTING_DEPOSIT), amount=Decimal('20'),
                adjudication_id='A-0', confirmed_by='u-bank-1', project_version=1,
                created_at=datetime(2026, 9, 1), declared_total_price=Decimal('100'),
            )
        )
        action = FundAction(LedgerKind.EXISTING_DEPOSIT, Decimal('30'), declared_total_price=Decimal('100'))
        verdict = adjudicate(
            action, empty_view(), ledger, eligibility_adjudication(Outcome.EXISTING_HOME)
        )
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('定金上限' in i for i in verdict.intercepts))
        self.assertEqual(verdict.quota_changes, ())

    def test_release_before_joint_acceptance_is_denied(self) -> None:
        ledger = ledger_with((LedgerKind.PRESALE_FUNDS, '500'))
        action = FundAction(LedgerKind.SUPERVISION_RELEASE, Decimal('100'))
        verdict = adjudicate(
            action, empty_view(), ledger, eligibility_adjudication(), approved=True
        )
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('联合验收前不得解除' in i for i in verdict.intercepts))

    def test_release_requires_approval(self) -> None:
        view = view_with((FactKind.JOINT_ACCEPTANCE, date(2026, 8, 20)))
        ledger = ledger_with((LedgerKind.PRESALE_FUNDS, '500'))
        action = FundAction(LedgerKind.SUPERVISION_RELEASE, Decimal('100'))
        verdict = adjudicate(
            action, view, ledger, eligibility_adjudication(Outcome.EXISTING_HOME), approved=False
        )
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('审批' in i for i in verdict.intercepts))

    def test_release_allowed_with_acceptance_and_approval(self) -> None:
        view = view_with((FactKind.JOINT_ACCEPTANCE, date(2026, 8, 20)))
        ledger = ledger_with((LedgerKind.PRESALE_FUNDS, '500'))
        action = FundAction(LedgerKind.SUPERVISION_RELEASE, Decimal('100'))
        verdict = adjudicate(
            action, view, ledger, eligibility_adjudication(Outcome.EXISTING_HOME), approved=True
        )
        self.assertIs(verdict.outcome, Outcome.ALLOW)
        self.assertTrue(any(m.kind is FactKind.JOINT_ACCEPTANCE for m in verdict.materials))

    def test_entrusted_payment_over_balance_is_denied(self) -> None:
        ledger = ledger_with((LedgerKind.DEVELOP_LOAN, '100'))
        action = FundAction(LedgerKind.ENTRUSTED_PAYMENT, Decimal('200'))
        verdict = adjudicate(action, empty_view(), ledger)
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('余额' in i for i in verdict.intercepts))

    def test_seizure_blocks_outflow(self) -> None:
        view = view_with((FactKind.MORTGAGE_SEIZURE, date(2026, 7, 1)))
        ledger = ledger_with((LedgerKind.DEVELOP_LOAN, '500'))
        action = FundAction(LedgerKind.ENTRUSTED_PAYMENT, Decimal('100'))
        verdict = adjudicate(action, view, ledger)
        self.assertIs(verdict.outcome, Outcome.DENY)
        self.assertTrue(any('抵押查封' in i for i in verdict.intercepts))


if __name__ == '__main__':
    unittest.main()
