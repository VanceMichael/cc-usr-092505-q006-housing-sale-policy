import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from housing_sale_policy.temporal import Fact, FactKind, FactLog


def make_fact(fid, kind, on, seq, corrects=None, source="文号A", by="dev1"):
    return Fact(
        fact_id=fid,
        project_id="P1",
        kind=kind,
        occurred_on=on,
        source=source,
        submitted_by=by,
        seq=seq,
        corrects=corrects,
    )


class FactLogTest(unittest.TestCase):
    def test_latest_picks_most_recent_business_date(self) -> None:
        log = FactLog()
        log.append(make_fact("F1", FactKind.TOPPING_OUT, date(2025, 3, 1), 1))
        log.append(make_fact("F2", FactKind.TOPPING_OUT, date(2025, 5, 1), 2))
        self.assertEqual(log.latest(FactKind.TOPPING_OUT).fact_id, "F2")
        self.assertIsNone(log.latest(FactKind.JOINT_ACCEPTANCE))

    def test_correction_keeps_old_fact_and_excludes_it_from_active(self) -> None:
        log = FactLog()
        log.append(make_fact("F1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 1), 1, source="原公告"))
        log.append(
            make_fact("F2", FactKind.LAND_ANNOUNCEMENT, date(2024, 7, 1), 2,
                      corrects="F1", source="更正公告")
        )
        self.assertEqual(len(log.all()), 2)  # 旧事实保留
        self.assertTrue(log.is_corrected("F1"))
        latest = log.latest(FactKind.LAND_ANNOUNCEMENT)
        self.assertEqual(latest.fact_id, "F2")
        self.assertEqual(latest.source, "更正公告")

    def test_duplicate_id_and_missing_corrects_rejected(self) -> None:
        log = FactLog()
        log.append(make_fact("F1", FactKind.TOPPING_OUT, date(2025, 1, 1), 1))
        with self.assertRaises(ValueError):
            log.append(make_fact("F1", FactKind.TOPPING_OUT, date(2025, 2, 1), 2))
        with self.assertRaises(ValueError):
            log.append(make_fact("F3", FactKind.TOPPING_OUT, date(2025, 2, 1), 3, corrects="FX"))

    def test_fact_keeps_source_and_submitter(self) -> None:
        log = FactLog()
        log.append(make_fact("F1", FactKind.PLANNING_PERMIT, date(2024, 3, 1), 1,
                             source="规自局文号", by="staff1"))
        fact = log.latest(FactKind.PLANNING_PERMIT)
        self.assertEqual(fact.source, "规自局文号")
        self.assertEqual(fact.submitted_by, "staff1")


if __name__ == "__main__":
    unittest.main()
