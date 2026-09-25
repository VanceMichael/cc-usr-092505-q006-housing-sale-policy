import dataclasses
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from housing_sale_policy.facts import (
    EvidenceSource,
    FactKind,
    FactLog,
    MaterialRef,
    TemporalFact,
)


def make_fact(fact_id: str, kind: FactKind, occurred_on: date) -> TemporalFact:
    return TemporalFact(
        fact_id=fact_id,
        kind=kind,
        occurred_on=occurred_on,
        source=EvidenceSource('DOC-1', '自然资源局', 'u-dev-1', '开发企业'),
        recorded_at=datetime(2026, 9, 1, 10, 0, 0),
    )


class FactLogTest(unittest.TestCase):
    def test_append_bumps_version_and_view_reads(self) -> None:
        log = FactLog()
        self.assertEqual(log.version, 0)
        log.append(make_fact('F-1', FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1)))
        log.append(make_fact('F-2', FactKind.STRUCTURE_TOPPING, date(2026, 8, 1)))
        self.assertEqual(log.version, 2)
        view = log.view()
        self.assertTrue(view.has(FactKind.STRUCTURE_TOPPING))
        self.assertFalse(view.has(FactKind.JOINT_ACCEPTANCE))
        self.assertEqual(view.latest(FactKind.LAND_ANNOUNCEMENT).fact_id, 'F-1')

    def test_latest_returns_last_recorded_fact(self) -> None:
        log = FactLog()
        log.append(make_fact('F-1', FactKind.PARTIAL_ACCEPTANCE, date(2026, 5, 1)))
        log.append(make_fact('F-2', FactKind.PARTIAL_ACCEPTANCE, date(2026, 6, 1)))
        self.assertEqual(log.view().latest(FactKind.PARTIAL_ACCEPTANCE).fact_id, 'F-2')

    def test_fact_is_immutable(self) -> None:
        fact = make_fact('F-1', FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            fact.occurred_on = date(2026, 4, 1)

    def test_material_ref_captures_source(self) -> None:
        fact = make_fact('F-1', FactKind.LAND_ANNOUNCEMENT, date(2026, 3, 1))
        ref = MaterialRef.of(fact)
        self.assertEqual(ref.document_no, 'DOC-1')
        self.assertEqual(ref.issuer, '自然资源局')
        self.assertEqual(ref.submitted_by, 'u-dev-1')
        self.assertEqual(ref.kind, FactKind.LAND_ANNOUNCEMENT)


if __name__ == '__main__':
    unittest.main()
