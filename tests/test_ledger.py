import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from housing_sale_policy.ledger import FundKind, LedgerEntry, ProjectLedger


def entry(eid, kind, amount, seq, reverses=None):
    return LedgerEntry(
        entry_id=eid,
        project_id="P1",
        kind=kind,
        amount=Decimal(str(amount)),
        occurred_on=date(2026, 1, 1),
        confirmed_by="bank1",
        seq=seq,
        reverses=reverses,
    )


class ProjectLedgerTest(unittest.TestCase):
    def test_supervised_balance_tracks_inflow_and_outflow(self) -> None:
        ledger = ProjectLedger("P1")
        ledger.append(entry("E1", FundKind.PRESALE_PROCEEDS, 100, 1))
        ledger.append(entry("E2", FundKind.MORTGAGE_DISBURSEMENT, 50, 2))
        ledger.append(entry("E3", FundKind.ENTRUSTED_PAYMENT, 30, 3))
        self.assertEqual(ledger.supervised_balance(), Decimal(120))

    def test_non_supervised_kinds_do_not_affect_balance(self) -> None:
        ledger = ProjectLedger("P1")
        ledger.append(entry("E1", FundKind.DEVELOPMENT_LOAN, 500, 1))
        ledger.append(entry("E2", FundKind.OWN_FUNDS, 200, 2))
        ledger.append(entry("E3", FundKind.EXISTING_HOME_DEPOSIT, 20, 3))
        self.assertEqual(ledger.supervised_balance(), Decimal(0))
        self.assertEqual(ledger.total_of(FundKind.DEVELOPMENT_LOAN), Decimal(500))

    def test_reversal_offsets_original_and_keeps_it(self) -> None:
        ledger = ProjectLedger("P1")
        ledger.append(entry("E1", FundKind.PRESALE_PROCEEDS, 100, 1))
        ledger.append(entry("E2", FundKind.REVERSAL, 100, 2, reverses="E1"))
        self.assertEqual(ledger.supervised_balance(), Decimal(0))
        self.assertEqual(len(ledger.all()), 2)  # 原条目保留
        self.assertTrue(ledger.is_reversed("E1"))

    def test_double_reversal_and_reversing_reversal_rejected(self) -> None:
        ledger = ProjectLedger("P1")
        ledger.append(entry("E1", FundKind.PRESALE_PROCEEDS, 100, 1))
        ledger.append(entry("E2", FundKind.REVERSAL, 100, 2, reverses="E1"))
        with self.assertRaises(ValueError):
            ledger.append(entry("E3", FundKind.REVERSAL, 100, 3, reverses="E1"))
        with self.assertRaises(ValueError):
            ledger.append(entry("E4", FundKind.REVERSAL, 100, 4, reverses="E2"))

    def test_non_positive_amount_rejected(self) -> None:
        with self.assertRaises(ValueError):
            entry("E1", FundKind.OWN_FUNDS, 0, 1)

    def test_supervision_released_flag(self) -> None:
        ledger = ProjectLedger("P1")
        ledger.append(entry("E1", FundKind.PRESALE_PROCEEDS, 100, 1))
        self.assertFalse(ledger.supervision_released())
        ledger.append(entry("E2", FundKind.SUPERVISION_RELEASE, 100, 2))
        self.assertTrue(ledger.supervision_released())
        self.assertEqual(ledger.supervised_balance(), Decimal(0))


if __name__ == "__main__":
    unittest.main()
