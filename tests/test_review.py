import unittest
from datetime import date
from decimal import Decimal

from helpers import AS_OF, BANK, DEVELOPER, PROJECT_ID, make_service, seed_new_project
from housing_sale_policy.adjudication import FundAction, Outcome
from housing_sale_policy.ledger import LedgerKind
from housing_sale_policy.policy import ProjectClass
from housing_sale_policy.review import explain_action, explain_project


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()
        seed_new_project(self.service)
        self.eligibility = self.service.assess_eligibility(PROJECT_ID, DEVELOPER, AS_OF)
        self.service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.DEVELOP_LOAN, Decimal('1000')), BANK, AS_OF
        )
        self.allowed = self.service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.ENTRUSTED_PAYMENT, Decimal('100')), BANK, AS_OF
        )
        self.denied = self.service.request_fund_action(
            PROJECT_ID, FundAction(LedgerKind.ENTRUSTED_PAYMENT, Decimal('2000')), BANK, AS_OF
        )

    def test_explain_eligibility_lists_policy_dates_and_materials(self) -> None:
        explanation = explain_action(self.service, PROJECT_ID, self.eligibility.adjudication_id)
        self.assertEqual(explanation.subject, '资格裁定')
        self.assertEqual(explanation.outcome, '可申请预售')
        hit = explanation.policy_hits[0]
        self.assertEqual(hit.effective_from, date(2026, 1, 1))
        self.assertIs(hit.classification, ProjectClass.NEW)
        self.assertIn(('土地公告日', date(2026, 3, 1)), hit.anchor_dates)
        documents = {m.document_no for m in explanation.materials}
        self.assertEqual(documents, {'GG-2026-01', 'FD-2026-08'})

    def test_explain_allowed_action_lists_quota_change_and_entry(self) -> None:
        explanation = explain_action(self.service, PROJECT_ID, self.allowed.adjudication_id)
        self.assertEqual(explanation.outcome, '允许')
        change = explanation.quota_changes[0]
        self.assertEqual((change.account, change.before, change.after),
                         ('监管账户', Decimal('1000'), Decimal('900')))
        self.assertIsNotNone(explanation.ledger_entry_id)
        self.assertEqual(explanation.confirmed_by, BANK.actor_id)
        self.assertTrue(explanation.policy_hits)

    def test_explain_denied_action_lists_intercept_reason(self) -> None:
        explanation = explain_action(self.service, PROJECT_ID, self.denied.adjudication_id)
        self.assertEqual(explanation.outcome, '拒绝')
        self.assertTrue(any('余额' in reason for reason in explanation.intercepts))
        self.assertIsNone(explanation.ledger_entry_id)
        self.assertEqual(explanation.quota_changes, ())

    def test_explain_project_covers_all_adjudications(self) -> None:
        explanation = explain_project(self.service, PROJECT_ID)
        self.assertEqual(explanation.project_id, PROJECT_ID)
        self.assertEqual(explanation.supervised_balance, Decimal('900'))
        self.assertEqual(len(explanation.actions), len(self.service.adjudications(PROJECT_ID)))
        outcomes = [action.outcome for action in explanation.actions]
        self.assertEqual(outcomes.count('允许'), 2)
        self.assertEqual(outcomes.count('拒绝'), 1)


if __name__ == '__main__':
    unittest.main()
