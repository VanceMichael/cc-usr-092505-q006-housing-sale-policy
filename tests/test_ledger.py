import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from housing_sale_policy.ledger import (
    Direction,
    LedgerEntry,
    LedgerKind,
    ProjectLedger,
    direction_of,
)


def make_entry(entry_id: str, kind: LedgerKind, amount: str, **kwargs) -> LedgerEntry:
    return LedgerEntry(
        entry_id=entry_id,
        project_id='P1',
        kind=kind,
        direction=direction_of(kind),
        amount=Decimal(amount),
        adjudication_id='A-1',
        confirmed_by='u-bank-1',
        project_version=1,
        created_at=datetime(2026, 9, 1),
        **kwargs,
    )


class ProjectLedgerTest(unittest.TestCase):
    def test_append_and_balance(self) -> None:
        ledger = ProjectLedger()
        ledger.append(make_entry('L-1', LedgerKind.DEVELOP_LOAN, '1000'))
        ledger.append(make_entry('L-2', LedgerKind.PRESALE_FUNDS, '500'))
        ledger.append(make_entry('L-3', LedgerKind.ENTRUSTED_PAYMENT, '200'))
        self.assertEqual(ledger.balance(), Decimal('1300'))
        self.assertEqual(ledger.count, 3)

    def test_total_by_kind(self) -> None:
        ledger = ProjectLedger()
        ledger.append(make_entry('L-1', LedgerKind.PRESALE_FUNDS, '300'))
        ledger.append(make_entry('L-2', LedgerKind.PRESALE_FUNDS, '200'))
        ledger.append(make_entry('L-3', LedgerKind.OWN_FUNDS, '100'))
        self.assertEqual(ledger.total(LedgerKind.PRESALE_FUNDS), Decimal('500'))

    def test_rejects_non_positive_amount(self) -> None:
        ledger = ProjectLedger()
        with self.assertRaises(ValueError):
            ledger.append(make_entry('L-1', LedgerKind.OWN_FUNDS, '0'))

    def test_declared_price_total_for_deposit_cap(self) -> None:
        ledger = ProjectLedger()
        ledger.append(
            make_entry('L-1', LedgerKind.EXISTING_DEPOSIT, '20', declared_total_price=Decimal('100'))
        )
        ledger.append(
            make_entry('L-2', LedgerKind.EXISTING_DEPOSIT, '10', declared_total_price=Decimal('50')))
        self.assertEqual(ledger.total_declared_price(), Decimal('150'))

    def test_direction_mapping(self) -> None:
        self.assertIs(direction_of(LedgerKind.ENTRUSTED_PAYMENT), Direction.OUT)
        self.assertIs(direction_of(LedgerKind.SUPERVISION_RELEASE), Direction.OUT)
        self.assertIs(direction_of(LedgerKind.MORTGAGE_DISBURSEMENT), Direction.IN)


if __name__ == '__main__':
    unittest.main()
