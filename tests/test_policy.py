import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from housing_sale_policy.facts import EvidenceSource, FactKind, FactLog, TemporalFact
from housing_sale_policy.policy import (
    Eligibility,
    PolicyBook,
    PolicyVersion,
    ProjectClass,
    evaluate_eligibility,
)

AS_OF = date(2026, 9, 1)


def make_policy(effective_from: date = date(2026, 1, 1), version: int = 1) -> PolicyVersion:
    return PolicyVersion(
        policy_id='商品住房销售政策',
        version=version,
        effective_from=effective_from,
        deposit_cap_ratio=Decimal('0.2'),
    )


def view_of(*facts: tuple[FactKind, date]) -> FactLog:
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


class EligibilityTest(unittest.TestCase):
    def test_new_project_topped_is_presale_eligible(self) -> None:
        view = view_of(
            (FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1)),
            (FactKind.STRUCTURE_TOPPING, date(2026, 8, 1)),
        )
        report = evaluate_eligibility(view, make_policy(), AS_OF)
        self.assertEqual(report.outcome, Eligibility.PRESALE)
        self.assertEqual(report.policy_hit.classification, ProjectClass.NEW)
        self.assertIn(('土地公告日', date(2026, 3, 1)), report.policy_hit.anchor_dates)
        self.assertEqual(len(report.materials), 2)

    def test_new_project_without_topping_is_denied(self) -> None:
        view = view_of((FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1)))
        report = evaluate_eligibility(view, make_policy(), AS_OF)
        self.assertEqual(report.outcome, Eligibility.NOT_ELIGIBLE)
        self.assertTrue(any('主体封顶' in reason for reason in report.reasons))

    def test_in_transition_project_needs_district_review(self) -> None:
        view = view_of(
            (FactKind.LAND_ANNOUNCEMENT, date(2025, 6, 1)),
            (FactKind.LOAN_CONTRACT, date(2025, 12, 1)),
            (FactKind.STRUCTURE_TOPPING, date(2026, 8, 1)),
        )
        report = evaluate_eligibility(view, make_policy(), AS_OF)
        self.assertEqual(report.outcome, Eligibility.DISTRICT_REVIEW)
        self.assertEqual(report.policy_hit.classification, ProjectClass.IN_TRANSITION)
        self.assertIn(('贷款合同日', date(2025, 12, 1)), report.policy_hit.anchor_dates)
        self.assertTrue(any('区政府评估' in reason for reason in report.reasons))

    def test_completed_project_is_existing_home(self) -> None:
        view = view_of(
            (FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1)),
            (FactKind.JOINT_ACCEPTANCE, date(2026, 8, 20)),
        )
        report = evaluate_eligibility(view, make_policy(), AS_OF)
        self.assertEqual(report.outcome, Eligibility.EXISTING_HOME)
        self.assertEqual(report.materials[0].kind, FactKind.JOINT_ACCEPTANCE)

    def test_seizure_blocks_sales(self) -> None:
        view = view_of(
            (FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1)),
            (FactKind.STRUCTURE_TOPPING, date(2026, 8, 1)),
            (FactKind.MORTGAGE_SEIZURE, date(2026, 7, 15)),
        )
        report = evaluate_eligibility(view, make_policy(), AS_OF)
        self.assertEqual(report.outcome, Eligibility.NOT_ELIGIBLE)
        self.assertTrue(any('抵押查封' in reason for reason in report.reasons))

    def test_missing_announcement_is_denied(self) -> None:
        view = view_of((FactKind.STRUCTURE_TOPPING, date(2026, 8, 1)))
        report = evaluate_eligibility(view, make_policy(), AS_OF)
        self.assertEqual(report.outcome, Eligibility.NOT_ELIGIBLE)
        self.assertTrue(any('土地公告' in reason for reason in report.reasons))


class PolicyBookTest(unittest.TestCase):
    def test_effective_selects_latest_version_before_as_of(self) -> None:
        book = PolicyBook()
        book.register(make_policy(effective_from=date(2026, 1, 1), version=1))
        book.register(make_policy(effective_from=date(2026, 7, 1), version=2))
        self.assertEqual(book.effective(date(2026, 3, 1)).version, 1)
        self.assertEqual(book.effective(date(2026, 9, 1)).version, 2)

    def test_effective_without_policy_raises(self) -> None:
        book = PolicyBook()
        book.register(make_policy(effective_from=date(2026, 1, 1)))
        with self.assertRaises(LookupError):
            book.effective(date(2025, 12, 31))

    def test_corrected_version_replaces_corrected_one(self) -> None:
        book = PolicyBook()
        book.register(make_policy(effective_from=date(2026, 1, 1), version=1))
        corrected = PolicyVersion(
            policy_id='商品住房销售政策',
            version=2,
            effective_from=date(2025, 1, 1),
            deposit_cap_ratio=Decimal('0.2'),
            corrects=1,
        )
        book.register(corrected)
        # 被更正版本不再参与选用，更正版本追溯适用
        self.assertIs(book.effective(date(2026, 9, 1)), corrected)
        self.assertIs(book.effective(date(2025, 6, 1)), corrected)


if __name__ == '__main__':
    unittest.main()
