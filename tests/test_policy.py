import unittest
from datetime import date

from support import CUTOFF, NEW_POLICY, OLD_POLICY, make_book

from housing_sale_policy.policy import PolicyBook
from housing_sale_policy.ruling import adjudicate_project, classify
from housing_sale_policy.temporal import Fact, FactKind, FactLog


def log_with(*facts) -> FactLog:
    log = FactLog()
    for seq, (fid, kind, on) in enumerate(facts, start=1):
        log.append(
            Fact(
                fact_id=fid,
                project_id="P1",
                kind=kind,
                occurred_on=on,
                source="测试文号",
                submitted_by="dev1",
                seq=seq,
            )
        )
    return log


class PolicyBookTest(unittest.TestCase):
    def test_effective_on_picks_latest_effective_version(self) -> None:
        book = make_book()
        self.assertIs(book.effective_on(date(2021, 5, 1)), OLD_POLICY)
        self.assertIs(book.effective_on(CUTOFF), NEW_POLICY)
        self.assertIs(book.effective_on(date(2026, 1, 1)), NEW_POLICY)

    def test_effective_on_before_any_version_raises(self) -> None:
        with self.assertRaises(ValueError):
            make_book().effective_on(date(2019, 12, 31))

    def test_duplicate_version_ids_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PolicyBook([OLD_POLICY, OLD_POLICY])

    def test_get_returns_version_by_id(self) -> None:
        book = make_book()
        self.assertIs(book.get("P2024"), NEW_POLICY)
        with self.assertRaises(KeyError):
            book.get("PX")


class ClassifyTest(unittest.TestCase):
    def test_land_announcement_after_cutoff_is_new_project(self) -> None:
        log = log_with(("F1", FactKind.LAND_ANNOUNCEMENT, date(2024, 7, 1)))
        classification, hits, used = classify(log, NEW_POLICY)
        self.assertEqual(classification, "new")
        self.assertEqual(hits[0].cutoff, CUTOFF)
        self.assertEqual(hits[0].project_date, date(2024, 7, 1))
        self.assertEqual(used[0].fact_id, "F1")

    def test_land_announcement_before_cutoff_is_in_transit(self) -> None:
        log = log_with(("F1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 1)))
        classification, _, _ = classify(log, NEW_POLICY)
        self.assertEqual(classification, "in_transit")

    def test_planning_permit_is_fallback_anchor(self) -> None:
        log = log_with(("F1", FactKind.PLANNING_PERMIT, date(2024, 8, 1)))
        classification, hits, _ = classify(log, NEW_POLICY)
        self.assertEqual(classification, "new")
        self.assertEqual(hits[-1].anchor, FactKind.PLANNING_PERMIT.value)

    def test_no_anchor_facts_is_unknown(self) -> None:
        log = log_with(("F1", FactKind.TOPPING_OUT, date(2025, 1, 1)))
        classification, _, _ = classify(log, NEW_POLICY)
        self.assertEqual(classification, "unknown")


class AdjudicateTest(unittest.TestCase):
    def test_unknown_classification_is_rejected(self) -> None:
        log = log_with(("F1", FactKind.TOPPING_OUT, date(2025, 1, 1)))
        route, _, _, _, intercepts = adjudicate_project(
            log=log, policy=NEW_POLICY, supervision_opened=False
        )
        self.assertEqual(route.value, "rejected")
        self.assertTrue(any("无法划分新旧政策" in item for item in intercepts))

    def test_contradictory_facts_go_to_district_review(self) -> None:
        log = log_with(
            ("F1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 1)),
            ("F2", FactKind.TOPPING_OUT, date(2025, 6, 1)),
            ("F3", FactKind.JOINT_ACCEPTANCE, date(2025, 5, 1)),
        )
        route, _, _, materials, intercepts = adjudicate_project(
            log=log, policy=NEW_POLICY, supervision_opened=True
        )
        self.assertEqual(route.value, "district_review")
        self.assertTrue(any("事实矛盾" in item for item in intercepts))
        self.assertEqual(len(materials), 3)

    def test_existing_home_filing_needs_joint_acceptance_and_first_registration(self) -> None:
        log = log_with(
            ("F1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 1)),
            ("F2", FactKind.JOINT_ACCEPTANCE, date(2025, 9, 1)),
            ("F3", FactKind.FIRST_REGISTRATION, date(2025, 10, 1)),
        )
        route, _, _, _, _ = adjudicate_project(
            log=log, policy=NEW_POLICY, supervision_opened=True
        )
        self.assertEqual(route.value, "existing_home_filing")


if __name__ == "__main__":
    unittest.main()
