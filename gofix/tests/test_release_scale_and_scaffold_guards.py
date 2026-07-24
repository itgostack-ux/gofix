import json
from pathlib import Path
from unittest import TestCase


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent


class TestReleaseScaleAndScaffoldGuards(TestCase):
	def test_technician_report_and_costing_are_set_based(self):
		report = (
			PACKAGE_ROOT
			/ "gofix_services/report/technician_performance/technician_performance.py"
		).read_text()
		costing = (PACKAGE_ROOT / "overrides/sales_order.py").read_text()
		self.assertIn("WITH scoped_assignments AS", report)
		self.assertIn("completed_service_orders AS", report)
		self.assertNotIn("for row in data:", report)
		costing = costing[costing.index("def _update_service_costing"):costing.index("def _alert_max_rework")]
		self.assertNotIn('frappe.db.get_value("Employee"', costing)
		self.assertIn("employee_rates", costing)

	def test_ops_and_token_user_enrichment_is_batched_and_bounded(self):
		ops = (
			PACKAGE_ROOT
			/ "gofix_services/page/gofix_ops_hub/gofix_ops_hub.py"
		).read_text()
		tokens = (PACKAGE_ROOT / "../gofix/api/token_api.py").resolve().read_text()
		self.assertIn("ops_hub_ticket_queue_limit", ops)
		self.assertIn("ops_hub_related_row_limit", ops)
		self.assertNotIn("frappe.utils.get_fullname(row.changed_by)", ops)
		self.assertIn("missing_names", tokens)
		self.assertIn("is_truncated = len(raw_rows) > analytics_limit", tokens)

	def test_authoritative_scope_failures_do_not_fall_back(self):
		source = (PACKAGE_ROOT / "security.py").read_text()
		self.assertIn("except (ImportError, ModuleNotFoundError):", source)
		self.assertIn("GoFix authoritative user-scope resolution failed", source)
		self.assertIn('return {"companies": set(), "warehouses": set()}', source)
		self.assertNotIn("from ch_item_master.security import get_user_allowed_companies\nexcept Exception", source)

	def test_release_scaffolds_and_tenant_setup_are_not_shipped(self):
		obsolete = (
			"../quick_fix.py",
			"e2e_water_damage_demo.py",
			"fix_workflow_states.py",
			"fixtures/sample_data.py",
			"setup_service_order_workflow.py",
			"tmp_custody_e2e.py",
			"tmp_custody_setup.py",
			"verify_workflow_setup.py",
			"setup/setup_gofix_company.py",
		)
		for relative_path in obsolete:
			with self.subTest(path=relative_path):
				self.assertFalse((PACKAGE_ROOT / relative_path).resolve().exists())

	def test_gofix_limits_are_declarative(self):
		settings = json.loads((
			PACKAGE_ROOT
			/ "gofix_services/doctype/gofix_settings/gofix_settings.json"
		).read_text())
		fields = {row.get("fieldname") for row in settings["fields"]}
		self.assertTrue({
			"service_queue_row_limit",
			"ops_hub_ticket_queue_limit",
			"ops_hub_related_row_limit",
			"interactive_report_row_limit",
			"annual_working_hours",
		}.issubset(fields))

	def test_company_fields_do_not_mutate_tenant_defaults(self):
		source = (PACKAGE_ROOT / "setup/company_custom_fields.py").read_text()
		self.assertNotIn("BESTBUY", source.upper())
		self.assertNotIn("_auto_enable_gofix_companies", source)
		self.assertNotIn("_backfill_store_code_prefix", source)
		self.assertNotIn("Repair Service'", source)
