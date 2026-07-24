from unittest import TestCase
import inspect
from unittest.mock import Mock, patch

import frappe

from gofix import config, security
from gofix.gofix import utils
from gofix.gofix_services import api
from gofix.api import token_api
from gofix.gofix_services.doctype.job_assignment import job_assignment
from gofix.gofix_services.doctype.service_request import service_request
from gofix.gofix_services.page.store_queue import store_queue


class TestReleaseAuthorizationRegressions(TestCase):
	def test_administrator_is_immutable_privileged_user(self):
		with patch.object(config, "get_setting", return_value="Service User"):
			self.assertTrue(config.is_privileged_user("Administrator"))
			self.assertTrue(config.has_role_setting("app_access_roles", user="Administrator"))

	def test_app_access_is_configured_and_guest_is_denied(self):
		with patch("gofix.config.has_role_setting", return_value=False) as allowed:
			self.assertFalse(utils.has_app_permission("Guest"))
			allowed.assert_called_once_with("app_access_roles", defaults=(), user="Guest")

	def test_manager_without_explicit_scope_gets_deny_query(self):
		with (
			patch.object(security, "_can_access_service_requests", return_value=True),
			patch.object(security, "_get_user_service_scope", return_value={"companies": set(), "warehouses": set()}),
		):
			self.assertEqual(security.get_service_request_query("manager@example.com"), "1=0")

	def test_named_service_request_outside_store_scope_is_denied(self):
		doc = frappe._dict({
			"name": "SR-OUTSIDE",
			"company": "Company A",
			"source_warehouse": "Store B",
		})
		with (
			patch.object(security, "_can_access_service_requests", return_value=True),
			patch.object(
				security,
				"_get_user_service_scope",
				return_value={"companies": {"Company A"}, "warehouses": {"Store A"}},
			),
		):
			self.assertFalse(
				security.has_service_request_permission(doc, user="manager@example.com", permission_type="write")
			)

	def test_named_service_request_guard_raises_on_scope_failure(self):
		doc = frappe._dict({"name": "SR-OUTSIDE"})
		with (
			patch.object(security.frappe, "get_doc", return_value=doc),
			patch.object(security.frappe, "has_permission", return_value=True),
			patch.object(security, "has_service_request_permission", return_value=False),
			self.assertRaises(frappe.PermissionError),
		):
			security.assert_service_request_access("SR-OUTSIDE", permission_type="write")

	def test_service_order_guard_also_checks_linked_service_request_scope(self):
		service_order = Mock(is_service_order=1, service_request="SR-1")
		with (
			patch.object(api.frappe, "get_doc", return_value=service_order),
			patch.object(api, "assert_service_request_access") as scope_guard,
		):
			self.assertIs(api._get_scoped_service_order("SO-1", "write"), service_order)

		service_order.check_permission.assert_called_once_with("write")
		scope_guard.assert_called_once_with("SR-1", permission_type="write")

	def test_job_assignment_creation_denies_before_loading_service_request(self):
		with (
			patch.object(
				job_assignment,
				"require_role_setting",
				side_effect=frappe.PermissionError("denied"),
			),
			patch.object(job_assignment, "assert_service_request_access") as scope_guard,
		):
			with self.assertRaises(frappe.PermissionError):
				job_assignment.authorize_job_assignment_creation("SR-1", "EMP-1")
		scope_guard.assert_not_called()

	def test_tablet_config_defines_a_bounded_query_limit(self):
		source = inspect.getsource(token_api.get_tablet_config)
		self.assertIn('queue_limit = min(get_int_setting("token_queue_limit", 200), 2000)', source)
		self.assertIn("limit_page_length=queue_limit", source)

	def test_store_queue_detail_uses_named_scope_guard(self):
		source = inspect.getsource(store_queue.get_request_detail)
		self.assertIn('assert_service_request_access(sr_name, permission_type="read")', source)
		self.assertNotIn('has_permission("Service Request", sr_name', source)

	def test_advance_refund_reuses_the_locked_existing_entry(self):
		sr = frappe._dict({
			"name": "SR-1",
			"advance_amount": 500,
			"advance_refund_entry": "PE-1",
			"company": "Company A",
			"customer": "CUST-1",
		})
		sr.reload = Mock()
		sr.db_set = Mock()
		payment_entry = frappe._dict({
			"name": "PE-1",
			"paid_amount": 500,
			"received_amount": 500,
			"docstatus": 0,
			"workflow_state": "Pending Approval",
		})
		with (
			patch.object(api, "_require_service_manager_role"),
			patch.object(api, "assert_service_request_access", return_value=sr),
			patch.object(api.frappe.db, "get_value", return_value="SR-1") as get_value,
			patch.object(api.frappe.db, "exists", return_value=True),
			patch.object(api.frappe, "get_doc", return_value=payment_entry),
			patch.object(api.frappe, "new_doc") as new_doc,
		):
			result = api.process_advance_refund("SR-1", amount=500)

		self.assertTrue(result["already_exists"])
		self.assertEqual(result["payment_entry"], "PE-1")
		self.assertTrue(get_value.call_args.kwargs["for_update"])
		new_doc.assert_not_called()

	def test_advance_refund_never_forces_workflow_state(self):
		source = inspect.getsource(api.process_advance_refund)
		self.assertNotIn('db_set("workflow_state"', source)
		self.assertIn("_route_advance_refund_for_approval", source)

	def test_dead_orphan_bulk_utility_is_removed(self):
		self.assertFalse(hasattr(service_request, "bulk_create_so_for_orphans"))

	def test_job_reconciliation_reads_bounded_pages(self):
		source = inspect.getsource(job_assignment._bounded_rows)
		self.assertIn("limit_page_length=batch_limit", source)
		self.assertIn("start=start", source)
