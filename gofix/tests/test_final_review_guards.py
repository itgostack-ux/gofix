import inspect
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from gofix import config
from gofix.gofix_services.doctype.service_request import service_request
from gofix.gofix_services.doctype.gofix_sla_rule import gofix_sla_rule
from gofix.api import app_switcher
from gofix.api import token_api
from gofix import security
from gofix.overrides import sales_order


class TestFinalReviewGuards(TestCase):
	def _replacement_doc(self):
		return frappe._dict({
			"name": "SR-1",
			"original_serial_no": "OLD-1",
			"replacement_serial_no": "NEW-1",
			"substitution_approved_by": "manager@example.com",
			"substitution_exception_request": None,
			"status": "Open",
			"meta": SimpleNamespace(has_field=lambda field: field in {"serial_no", "status"}),
			"add_comment": Mock(),
		})

	def test_replacement_approval_rejects_caller_supplied_identity(self):
		doc = self._replacement_doc()
		with (
			patch.object(service_request, "require_role_setting"),
			patch.object(service_request, "_get_locked_service_request", return_value=doc),
			patch.object(service_request.frappe, "session", frappe._dict(user="manager@example.com")),
			patch.object(service_request.frappe.db, "set_value") as set_value,
			self.assertRaises(frappe.PermissionError),
		):
			service_request.approve_item_replacement("SR-1", approver_user="victim@example.com")
		set_value.assert_not_called()

	def test_replacement_completion_rejects_caller_supplied_identity(self):
		doc = self._replacement_doc()
		with (
			patch.object(service_request, "require_role_setting"),
			patch.object(service_request, "_get_locked_service_request", return_value=doc),
			patch.object(service_request.frappe, "session", frappe._dict(user="manager@example.com")),
			patch.object(service_request.frappe.db, "set_value") as set_value,
			self.assertRaises(frappe.PermissionError),
		):
			service_request.complete_item_replacement("SR-1", completed_by="victim@example.com")
		set_value.assert_not_called()

	def test_replacement_completion_cannot_swap_the_approved_serial(self):
		doc = self._replacement_doc()
		with (
			patch.object(service_request, "require_role_setting"),
			patch.object(service_request, "_get_locked_service_request", return_value=doc),
			patch.object(service_request.frappe, "session", frappe._dict(user="manager@example.com")),
			patch.object(service_request.frappe.db, "set_value") as set_value,
			self.assertRaises(frappe.ValidationError),
		):
			service_request.complete_item_replacement("SR-1", replacement_serial_no="FORGED-1")
		set_value.assert_not_called()

	def test_sensitive_replacement_mutations_are_post_only(self):
		for function in (
			service_request.request_item_replacement,
			service_request.approve_item_replacement,
			service_request.complete_item_replacement,
		):
			self.assertEqual(
				frappe.allowed_http_methods_for_whitelisted_func[function],
				["POST"],
			)

	def test_app_switcher_fails_closed_for_pages_without_roles(self):
		with (
			patch.object(app_switcher.frappe, "get_roles", return_value=["Service User"]),
			patch.object(app_switcher.frappe, "get_all", return_value=[]),
			patch.object(app_switcher, "is_privileged_user", return_value=False),
		):
			self.assertEqual(app_switcher.get_allowed_pages(), [])

	def test_guest_token_create_uses_narrow_capability_without_permission_bypass(self):
		doc = frappe._dict({"source": "Tablet", "company": "Company A", "store": "Store A"})
		with patch.object(security.frappe, "flags", frappe._dict()):
			self.assertFalse(
				security.has_gofix_token_permission(doc, user="Guest", permission_type="create")
			)
			security.frappe.flags.gofix_guest_token_creation = True
			self.assertTrue(
				security.has_gofix_token_permission(doc, user="Guest", permission_type="create")
			)
			self.assertFalse(
				security.has_gofix_token_permission(doc, user="Guest", permission_type="write")
			)
		self.assertNotIn("ignore_permissions", inspect.getsource(token_api.create_token))

	def test_token_company_enablement_fails_closed_without_schema(self):
		with patch.object(token_api.frappe.db, "has_column", return_value=False):
			self.assertFalse(token_api._company_is_gofix_enabled("Company A"))

	def test_token_analytics_queries_are_bounded_by_setting(self):
		for function in (token_api.get_dashboard_stats, token_api.get_reports):
			source = inspect.getsource(function)
			self.assertIn('get_int_setting("token_analytics_row_limit", 5000)', source)

	def test_business_notification_resolver_is_scoped_and_bounded(self):
		from ch_erp15.ch_erp15 import notification_router

		with (
			patch.object(config, "get_int_setting", return_value=2),
			patch.object(
				notification_router,
				"get_scoped_users",
				return_value=["z@example.com", "a@example.com", "b@example.com"],
			) as get_scoped_users,
			patch.object(
				notification_router,
				"filter_users_by_company",
				return_value=["z@example.com", "a@example.com", "b@example.com"],
			) as filter_users_by_company,
			patch.object(
				notification_router,
				"filter_business_notification_recipients",
				return_value=["z@example.com", "a@example.com", "b@example.com"],
			),
		):
			users = config.get_business_role_users(
				("Service Manager",),
				company="Company A",
				store="Store A",
			)

		self.assertEqual(users, ["a@example.com", "b@example.com"])
		get_scoped_users.assert_called_once_with(["Service Manager"], store="Store A")
		filter_users_by_company.assert_called_once_with(
			["z@example.com", "a@example.com", "b@example.com"],
			"Company A",
		)

	def test_sla_escalation_fails_closed_through_shared_scope_resolver(self):
		with (
			patch.object(
				gofix_sla_rule.frappe.db,
				"get_value",
				side_effect=[
					frappe._dict(company="Company A", source_warehouse="Warehouse A"),
					"Store A",
				],
			),
			patch.object(
				gofix_sla_rule,
				"get_business_role_users",
				return_value=["manager@example.com"],
			) as resolve,
		):
			users = gofix_sla_rule._scoped_escalation_users("Service Manager", "SR-1")

		self.assertEqual(users, ["manager@example.com"])
		resolve.assert_called_once_with(
			("Service Manager",),
			company="Company A",
			store="Store A",
		)
		self.assertNotIn("Has Role", inspect.getsource(gofix_sla_rule._scoped_escalation_users))

	def test_rework_alert_is_company_and_store_scoped(self):
		doc = frappe._dict(
			name="SO-1",
			service_request="SR-1",
			company="Company A",
		)
		with (
			patch.object(sales_order, "get_role_setting", return_value={"Service Manager"}),
			patch.object(
				sales_order.frappe.db,
				"get_value",
				side_effect=[
					frappe._dict(company="Company A", source_warehouse="Warehouse A"),
					"Store A",
				],
			),
			patch.object(
				sales_order,
				"get_business_role_users",
				return_value=["manager@example.com"],
			) as resolve,
			patch.object(sales_order.frappe, "publish_realtime") as publish,
		):
			sales_order._alert_max_rework(doc, 3, 3)

		resolve.assert_called_once_with(
			{"Service Manager"},
			company="Company A",
			store="Store A",
		)
		publish.assert_called_once()
