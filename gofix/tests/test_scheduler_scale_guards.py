import ast
import inspect
import json
import textwrap
from pathlib import Path
from unittest import TestCase

import frappe

from gofix.gofix_services import api
from gofix.gofix_services.doctype.gofix_sla_rule import gofix_sla_rule
from gofix.gofix_services.doctype.service_request import service_request
from gofix.gofix_services.page.gofix_ops_hub import gofix_ops_hub
from gofix.setup import workflow


class TestSchedulerScaleGuards(TestCase):
	@staticmethod
	def _loop_body_database_calls(function):
		tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
		calls = []
		for loop in (node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While))):
			for statement in loop.body:
				for call in (node for node in ast.walk(statement) if isinstance(node, ast.Call)):
					name = ast.unparse(call.func)
					if name in {"frappe.get_all", "frappe.get_list"} or name.startswith("frappe.db."):
						calls.append((call.lineno, name))
		return calls

	def test_scheduler_mutations_do_not_commit_the_request_transaction(self):
		for function in (
			service_request.flag_unclaimed_devices,
			service_request.auto_expire_stale_requests,
			api.expire_pending_estimates,
			gofix_sla_rule.check_gofix_sla_breach,
		):
			with self.subTest(function=function.__name__):
				self.assertNotIn("frappe.db.commit(", inspect.getsource(function))

	def test_daily_jobs_are_bounded_and_set_based(self):
		for function in (
			service_request.flag_unclaimed_devices,
			service_request.auto_expire_stale_requests,
			api.expire_pending_estimates,
		):
			source = inspect.getsource(function)
			self.assertIn("scheduler_batch_limit", source)
			self.assertIn("UPDATE `tab", source)
			self.assertNotIn("frappe.db.set_value", source)

	def test_sla_sweep_is_bounded_rotating_and_preloaded(self):
		source = inspect.getsource(gofix_sla_rule.check_gofix_sla_breach)
		self.assertIn("sla_scheduler_batch_limit", source)
		self.assertIn("gofix:sla_sweep_cursor", source)
		self.assertEqual(source.count("_load_active_sla_rules()"), 1)
		self.assertNotIn("get_sla_rule(", source)
		self.assertIn("ROW_NUMBER() OVER", source)

	def test_company_specific_sla_rule_wins_over_global_rule(self):
		global_rule = frappe._dict(
			name="GLOBAL",
			company=None,
			issue_category="Screen",
			priority="High",
			warranty_plan=None,
			warranty_status=None,
		)
		company_rule = frappe._dict(
			name="COMPANY",
			company="Company A",
			issue_category="Screen",
			priority="High",
			warranty_plan=None,
			warranty_status=None,
		)
		selected = gofix_sla_rule._select_sla_rule(
			[global_rule, company_rule],
			"Screen",
			"High",
			"Company A",
		)
		self.assertEqual(selected.name, "COMPANY")

	def test_repair_history_prefetches_related_documents(self):
		self.assertEqual(self._loop_body_database_calls(gofix_ops_hub.get_repair_history), [])
		source = inspect.getsource(gofix_ops_hub.get_repair_history)
		self.assertIn("repair_history_record_limit", source)
		self.assertIn("pos_by_mr", source)
		self.assertIn("prs_by_po", source)

	def test_system_manager_has_state_and_transition_parity_in_generator(self):
		state_rows = workflow.workflow_states_with_system_manager_parity()
		state_keys = {(row["state"], str(row["doc_status"])) for row in state_rows}
		sm_state_keys = {
			(row["state"], str(row["doc_status"]))
			for row in state_rows
			if row["allow_edit"] == "System Manager"
		}
		self.assertEqual(state_keys, sm_state_keys)

		transition_rows = workflow.workflow_transitions_with_system_manager_parity()
		transition_keys = {
			(row["state"], row["action"], row["next_state"], row["condition"])
			for row in transition_rows
		}
		sm_transition_keys = {
			(row["state"], row["action"], row["next_state"], row["condition"])
			for row in transition_rows
			if row["allowed"] == "System Manager"
		}
		self.assertEqual(transition_keys, sm_transition_keys)

	def test_system_manager_has_state_and_transition_parity_in_fixture(self):
		fixture_path = Path(workflow.__file__).resolve().parents[1] / "fixtures/workflow.json"
		fixture = json.loads(fixture_path.read_text())[0]
		state_keys = {(row["state"], str(row["doc_status"])) for row in fixture["states"]}
		sm_state_keys = {
			(row["state"], str(row["doc_status"]))
			for row in fixture["states"]
			if row["allow_edit"] == "System Manager"
		}
		self.assertEqual(state_keys, sm_state_keys)

		transition_keys = {
			(row["state"], row["action"], row["next_state"], row.get("condition") or "")
			for row in fixture["transitions"]
		}
		sm_transition_keys = {
			(row["state"], row["action"], row["next_state"], row.get("condition") or "")
			for row in fixture["transitions"]
			if row["allowed"] == "System Manager"
		}
		self.assertEqual(transition_keys, sm_transition_keys)

	def test_scheduler_settings_are_declared(self):
		settings_path = (
			Path(__file__).resolve().parents[1]
			/ "gofix_services/doctype/gofix_settings/gofix_settings.json"
		)
		settings = json.loads(settings_path.read_text())
		fieldnames = {field.get("fieldname") for field in settings["fields"]}
		self.assertTrue({
			"scheduler_batch_limit",
			"unclaimed_device_days",
			"stale_request_expiry_days",
			"sla_scheduler_batch_limit",
			"sla_rule_limit",
			"sla_level_2_percent",
			"sla_warning_repeat_seconds",
			"sla_escalation_repeat_seconds",
			"repair_history_record_limit",
		}.issubset(fieldnames))
