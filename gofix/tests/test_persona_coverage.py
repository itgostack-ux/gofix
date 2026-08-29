"""Acceptance contract for the questions each GoFix stakeholder must answer."""

import json
from pathlib import Path
from unittest import TestCase

import frappe


ROOT = Path(__file__).resolve().parents[1] / "gofix_services"


class TestGoFixPersonaCoverage(TestCase):
	def _roles(self, relative_path):
		with (ROOT / relative_path).open(encoding="utf-8") as source:
			return {row["role"] for row in json.load(source).get("roles", [])}

	def test_accounts_can_answer_revenue_cost_margin_and_override_questions(self):
		self.assertTrue(
			{"Accounts User", "Accounts Manager"}
			<= self._roles("report/repair_profitability/repair_profitability.json")
		)
		columns = {
			row["fieldname"]
			for row in __import__(
				"gofix.gofix_services.report.repair_profitability.repair_profitability",
				fromlist=["get_columns"],
			).get_columns()
		}
		self.assertTrue({"revenue", "spare_parts_cost", "repair_margin", "repair_margin_pct"} <= columns)

	def test_ops_can_answer_queue_stage_sla_and_location_questions(self):
		for page in ("gofix_ops_hub", "service_hub", "store_queue"):
			self.assertTrue((ROOT / "page" / page / f"{page}.json").exists())
		for report in ("gofix_ticket_stage_time", "gofix_ticket_status_by_location", "service_request_summary"):
			self.assertTrue((ROOT / "report" / report / f"{report}.json").exists())

	def test_technicians_can_answer_assignment_work_qc_and_performance_questions(self):
		self.assertTrue(
			{"Service Engineer", "Technician"}
			& self._roles("report/technician_performance/technician_performance.json")
		)
		for doctype in ("job_assignment", "gofix_qc_checklist", "spare_parts_usage"):
			self.assertTrue((ROOT / "doctype" / doctype / f"{doctype}.json").exists())

	def test_managers_can_answer_approval_sla_rework_and_capacity_questions(self):
		self.assertGreater(frappe.db.count("GoFix Approval Rule", {"is_active": 1}), 0)
		self.assertGreater(frappe.db.count("GoFix SLA Rule", {"is_active": 1}), 0)
		self.assertIn("Service Manager", self._roles("report/technician_performance/technician_performance.json"))

	def test_customers_can_answer_status_estimate_history_and_receipt_questions(self):
		self.assertTrue((Path(__file__).resolve().parents[1] / "www/track-repair/index.py").exists())
		self.assertTrue((ROOT / "doctype/estimate_version/estimate_version.json").exists())
		self.assertTrue(frappe.db.exists("Print Format", "GoFix Delivery Receipt"))
		self.assertTrue(frappe.db.exists("Print Format", "Device Received Receipt"))

	def test_ceo_can_answer_scale_margin_override_damage_and_rework_questions(self):
		self.assertIn("CEO", self._roles("report/ceo_repair_dashboard/ceo_repair_dashboard.json"))
		from gofix.gofix_services.report.ceo_repair_dashboard.ceo_repair_dashboard import get_columns
		columns = {row["fieldname"] for row in get_columns()}
		self.assertTrue(
			{"actual_billed", "price_override_amount", "technician_damage_cost", "rework_count"}
			<= columns
		)

	def test_scm_can_answer_demand_consumption_genealogy_and_quarantine_questions(self):
		roles = self._roles("report/gofix_removed_spares_register/gofix_removed_spares_register.json")
		self.assertTrue({"Stock User", "Stock Manager", "Purchase User", "Purchase Manager"} <= roles)
		self.assertTrue((ROOT / "doctype/spare_parts_usage/spare_parts_usage.json").exists())
		for company in frappe.get_all(
			"CH Store",
			filters={"disabled": 0, "is_service_enabled": 1},
			distinct=True,
			pluck="company",
		):
			self.assertTrue(frappe.db.get_value("Company", company, "damaged_stock_warehouse"))
