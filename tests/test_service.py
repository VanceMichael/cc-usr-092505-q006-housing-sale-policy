import json
import unittest
from datetime import date
from decimal import Decimal

from support import adjudicate, confirm, make_service, submit

from housing_sale_policy.ledger import FundKind
from housing_sale_policy.ruling import Route
from housing_sale_policy.service import VersionConflictError
from housing_sale_policy.temporal import FactKind


def presale_ready(service, project_id="P1", actor="dev1"):
    """使项目取得可申请预售的当前裁定，返回裁定编号。"""
    submit(service, project_id, FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 15), actor=actor)
    record = adjudicate(service, project_id)
    assert record.allowed
    return record.result_id


class RouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()

    def test_new_project_presale_after_topping_out_and_supervision(self) -> None:
        svc = self.svc
        submit(svc, "P2", FactKind.LAND_ANNOUNCEMENT, date(2024, 7, 1), actor="dev2")
        # 缺主体封顶与监管账户：新项目预售被拦截
        record = adjudicate(svc, "P2")
        ruling = svc.current_ruling("P2")
        self.assertEqual(ruling.route, Route.REJECTED)
        self.assertEqual(ruling.classification, "new")
        self.assertIn("新项目预售要求主体封顶", ruling.intercepts)
        # 命中的政策日期逐项记录
        hit = ruling.policy_hits[0]
        self.assertEqual(hit.rule_id, "P2024-c1")
        self.assertEqual(hit.cutoff, date(2024, 6, 1))
        self.assertEqual(hit.project_date, date(2024, 7, 1))
        # 补证：监管账户与主体封顶后重新裁定
        opened = svc.open_supervision(
            actor_id="bank1", project_id="P2",
            expected_version=svc.project_version("P2"),
        )
        self.assertTrue(opened.allowed)
        submit(svc, "P2", FactKind.TOPPING_OUT, date(2025, 12, 1), actor="dev2")
        adjudicate(svc, "P2")
        self.assertEqual(svc.current_ruling("P2").route, Route.PRESALE)

    def test_in_transit_project_presale_without_topping_out(self) -> None:
        submit(self.svc, "P1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 15))
        adjudicate(self.svc, "P1")
        ruling = self.svc.current_ruling("P1")
        self.assertEqual(ruling.route, Route.PRESALE)
        self.assertEqual(ruling.classification, "in_transit")

    def test_seizure_routes_to_district_review_until_lifted(self) -> None:
        svc = self.svc
        submit(svc, "P1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 15))
        submit(svc, "P1", FactKind.MORTGAGE_SEIZURE, date(2025, 5, 1),
               detail={"status": "active"})
        adjudicate(svc, "P1")
        ruling = svc.current_ruling("P1")
        self.assertEqual(ruling.route, Route.DISTRICT_REVIEW)
        self.assertTrue(any("抵押查封" in item for item in ruling.intercepts))
        submit(svc, "P1", FactKind.MORTGAGE_SEIZURE, date(2025, 8, 1),
               detail={"status": "lifted"})
        adjudicate(svc, "P1")
        self.assertEqual(svc.current_ruling("P1").route, Route.PRESALE)

    def test_existing_home_filing_after_joint_acceptance_and_registration(self) -> None:
        svc = self.svc
        submit(svc, "P1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 15))
        submit(svc, "P1", FactKind.JOINT_ACCEPTANCE, date(2025, 9, 1))
        submit(svc, "P1", FactKind.FIRST_REGISTRATION, date(2025, 10, 1))
        adjudicate(svc, "P1")
        self.assertEqual(svc.current_ruling("P1").route, Route.EXISTING_HOME_FILING)


class PermissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()

    def test_developer_can_only_submit_own_project(self) -> None:
        record = submit(self.svc, "P1", FactKind.TOPPING_OUT, date(2025, 1, 1), actor="dev2")
        self.assertFalse(record.allowed)
        self.assertIn("开发企业只能提交本项目材料", record.intercepts)

    def test_funds_must_be_confirmed_by_project_bank(self) -> None:
        record = confirm(self.svc, "P1", FundKind.OWN_FUNDS, 100, actor="dev1")
        self.assertFalse(record.allowed)
        record = confirm(self.svc, "P1", FundKind.OWN_FUNDS, 100, actor="bank2")
        self.assertFalse(record.allowed)
        record = confirm(self.svc, "P1", FundKind.OWN_FUNDS, 100, actor="bank1")
        self.assertTrue(record.allowed)

    def test_approver_must_differ_from_material_submitter(self) -> None:
        svc = self.svc
        submit(svc, "P1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 15), actor="staff1")
        record = adjudicate(svc, "P1", actor="staff1")
        self.assertFalse(record.allowed)
        self.assertTrue(any("职责分离" in item for item in record.intercepts))
        record = adjudicate(svc, "P1", actor="staff2")
        self.assertTrue(record.allowed)


class FundActionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()

    def test_presale_proceeds_require_current_ruling(self) -> None:
        svc = self.svc
        record = confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100)
        self.assertFalse(record.allowed)
        self.assertTrue(any("必须引用当时资格" in item for item in record.intercepts))
        ruling_id = presale_ready(svc)
        record = confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=ruling_id)
        self.assertTrue(record.allowed)
        self.assertEqual(svc.supervised_balance("P1"), Decimal(100))

    def test_rejected_payment_does_not_consume_quota(self) -> None:
        svc = self.svc
        ruling_id = presale_ready(svc)
        confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=ruling_id)
        record = confirm(svc, "P1", FundKind.ENTRUSTED_PAYMENT, 150)
        self.assertFalse(record.allowed)
        quota = record.quota_changes[0]
        self.assertEqual(quota.before, quota.after)  # 拒绝事项不占用额度
        self.assertEqual(svc.supervised_balance("P1"), Decimal(100))
        record = confirm(svc, "P1", FundKind.ENTRUSTED_PAYMENT, 60)
        self.assertTrue(record.allowed)
        self.assertEqual(svc.supervised_balance("P1"), Decimal(40))
        info = svc.review_decision(record.decision_id)
        self.assertEqual(info["quota_changes"][0]["before"], "100")
        self.assertEqual(info["quota_changes"][0]["after"], "40")

    def test_supervision_release_requires_joint_acceptance(self) -> None:
        svc = self.svc
        ruling_id = presale_ready(svc)
        confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=ruling_id)
        # 联合验收前解除监管：正是要防止的错误
        record = confirm(svc, "P1", FundKind.SUPERVISION_RELEASE, 100, ruling_id=ruling_id)
        self.assertFalse(record.allowed)
        self.assertTrue(any("联合验收前" in item for item in record.intercepts))
        self.assertEqual(svc.supervised_balance("P1"), Decimal(100))
        submit(svc, "P1", FactKind.JOINT_ACCEPTANCE, date(2026, 1, 1))
        record = confirm(svc, "P1", FundKind.SUPERVISION_RELEASE, 100, ruling_id=ruling_id)
        self.assertTrue(record.allowed)
        self.assertEqual(svc.supervised_balance("P1"), Decimal(0))

    def test_existing_home_deposit_cap_uses_qualification_policy(self) -> None:
        svc = self.svc
        submit(svc, "P1", FactKind.LAND_ANNOUNCEMENT, date(2024, 1, 15))
        submit(svc, "P1", FactKind.JOINT_ACCEPTANCE, date(2025, 9, 1))
        submit(svc, "P1", FactKind.FIRST_REGISTRATION, date(2025, 10, 1))
        ruling_id = adjudicate(svc, "P1").result_id
        detail = {"house_price": "1000000"}
        # 当时资格规定定金上限为价款 20%（20 万）
        record = confirm(svc, "P1", FundKind.EXISTING_HOME_DEPOSIT, 250000,
                         ruling_id=ruling_id, detail=detail)
        self.assertFalse(record.allowed)
        self.assertTrue(any("上限" in item for item in record.intercepts))
        record = confirm(svc, "P1", FundKind.EXISTING_HOME_DEPOSIT, 200000,
                         ruling_id=ruling_id, detail=detail)
        self.assertTrue(record.allowed)
        quota = record.quota_changes[0]
        self.assertEqual((quota.before, quota.after), (Decimal("200000.00"), Decimal("0.00")))

    def test_mortgage_disbursement_follows_loan_contract_date(self) -> None:
        svc = self.svc
        ruling_id = presale_ready(svc)
        # 贷款合同日晚于政策基准日：按揭须入监管账户
        record = confirm(svc, "P1", FundKind.MORTGAGE_DISBURSEMENT, 80,
                         ruling_id=ruling_id, detail={"loan_contract_date": "2025-01-01"})
        self.assertFalse(record.allowed)
        self.assertTrue(any("监管账户" in item for item in record.intercepts))
        anchors = [hit.anchor for hit in record.policy_hits]
        self.assertIn("loan_contract_date", anchors)
        record = confirm(svc, "P1", FundKind.MORTGAGE_DISBURSEMENT, 80, ruling_id=ruling_id,
                         detail={"loan_contract_date": "2025-01-01",
                                 "to_supervised_account": "true"})
        self.assertTrue(record.allowed)
        # 贷款合同日早于基准日：按旧规则放行
        record = confirm(svc, "P1", FundKind.MORTGAGE_DISBURSEMENT, 80,
                         ruling_id=ruling_id, detail={"loan_contract_date": "2024-01-01"})
        self.assertTrue(record.allowed)
        self.assertEqual(svc.supervised_balance("P1"), Decimal(160))


class ConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()

    def test_supplement_and_payment_serialize_on_same_project_version(self) -> None:
        svc = self.svc
        ruling_id = presale_ready(svc)
        confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=ruling_id)
        version = svc.project_version("P1")
        # 补证与付款并发：付款先按版本 v 提交成功
        paid = svc.confirm_funds(
            actor_id="bank1", project_id="P1", kind=FundKind.ENTRUSTED_PAYMENT,
            amount=Decimal(30), occurred_on=date(2026, 2, 1), expected_version=version,
        )
        self.assertTrue(paid.allowed)
        # 基于同一旧版本的补证发生冲突，须重新读取
        with self.assertRaises(VersionConflictError):
            svc.submit_fact(
                actor_id="dev1", project_id="P1", kind=FactKind.SECTION_ACCEPTANCE,
                occurred_on=date(2026, 1, 1), source="测试文号", expected_version=version,
            )
        retry = svc.submit_fact(
            actor_id="dev1", project_id="P1", kind=FactKind.SECTION_ACCEPTANCE,
            occurred_on=date(2026, 1, 1), source="测试文号",
            expected_version=svc.project_version("P1"),
        )
        self.assertTrue(retry.allowed)


class CorrectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()

    def test_rule_correction_creates_new_ruling_and_keeps_old(self) -> None:
        svc = self.svc
        old_id = presale_ready(svc)
        record = adjudicate(svc, "P1", corrects=old_id)
        new_id = record.result_id
        self.assertNotEqual(old_id, new_id)
        # 旧结论保留可查
        self.assertEqual(svc.get_ruling("P1", old_id).route, Route.PRESALE)
        self.assertEqual(svc.current_ruling("P1").supersedes, old_id)
        # 资金动作不得再引用旧资格
        record = confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=old_id)
        self.assertFalse(record.allowed)
        self.assertTrue(any("资格已更正" in item for item in record.intercepts))
        record = confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=new_id)
        self.assertTrue(record.allowed)

    def test_reversal_offsets_entry_and_keeps_original(self) -> None:
        svc = self.svc
        ruling_id = presale_ready(svc)
        inflow = confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=ruling_id)
        paid = confirm(svc, "P1", FundKind.ENTRUSTED_PAYMENT, 60)
        # 冲正预售款会使监管余额为负，须拦截
        record = svc.reverse_entry(
            actor_id="bank1", project_id="P1", entry_id=inflow.result_id,
            expected_version=svc.project_version("P1"), reason="测试",
        )
        self.assertFalse(record.allowed)
        # 冲正受托支付：余额回冲且原条目保留
        record = svc.reverse_entry(
            actor_id="bank1", project_id="P1", entry_id=paid.result_id,
            expected_version=svc.project_version("P1"), reason="测试",
        )
        self.assertTrue(record.allowed)
        self.assertEqual(svc.supervised_balance("P1"), Decimal(100))


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()

    def test_review_lists_policy_dates_materials_quota_and_intercepts(self) -> None:
        svc = self.svc
        submit(svc, "P2", FactKind.LAND_ANNOUNCEMENT, date(2024, 7, 1), actor="dev2")
        record = adjudicate(svc, "P2")
        info = svc.review_decision(record.decision_id)
        # 逐项列出：命中的政策日期、所用材料、拦截原因
        self.assertEqual(info["policy_hits"][0]["cutoff"], "2024-06-01")
        self.assertEqual(info["policy_hits"][0]["project_date"], "2024-07-01")
        self.assertEqual(info["materials"][0]["kind"], "land_announcement")
        self.assertEqual(info["materials"][0]["source"], "测试文号")
        self.assertTrue(any("主体封顶" in item for item in info["intercepts"]))
        json.dumps(info)  # 复核接口输出须可序列化

    def test_review_project_summarizes_qualification_and_decisions(self) -> None:
        svc = self.svc
        ruling_id = presale_ready(svc)
        paid = confirm(svc, "P1", FundKind.PRESALE_PROCEEDS, 100, ruling_id=ruling_id)
        overview = svc.review_project("P1")
        self.assertEqual(overview["current_ruling"]["route"], "presale")
        self.assertEqual(overview["supervised_balance"], "100")
        self.assertIn(paid.decision_id, overview["decisions"])
        json.dumps(overview)


if __name__ == "__main__":
    unittest.main()
