from unittest import TestCase
import inspect
from unittest.mock import Mock, patch

import frappe

from gofix import config, security
from gofix.gofix import utils
from gofix.gofix_services import api
from ch_pos.api import token_api
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
			allowed.assert_called_once_with("app_access_roles", user="Guest")

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

	def test_service_order_guard_still_checks_linked_service_request_scope(self):
		"""When a legacy order does exist, the ticket's scope is still asserted."""
		service_order = Mock(is_service_order=1, service_request="SR-1")
		with (
			patch.object(api.frappe.db, "exists", side_effect=lambda dt, *a: dt == "Sales Order"),
			patch.object(api.frappe, "get_doc", return_value=service_order),
			patch.object(api, "assert_service_request_access") as scope_guard,
		):
			self.assertIs(api._get_scoped_service_order("SO-1", "write"), service_order)

		service_order.check_permission.assert_called_once_with("write")
		scope_guard.assert_called_once_with("SR-1", permission_type="write")

	def test_superseded_order_endpoints_name_their_replacement(self):
		"""A repair has no Sales Order, so these say which endpoint to use.

		Every name in the map must resolve to something that exists, or the
		message sends the caller somewhere just as dead as where they started.
		"""
		import importlib

		self.assertTrue(api._SUPERSEDED_BY, "the superseded map went empty")
		for legacy, replacement in api._SUPERSEDED_BY.items():
			self.assertTrue(
				callable(getattr(api, legacy, None)),
				f"{legacy} is listed as superseded but no longer exists",
			)
			module_name, _sep, attr = replacement.rpartition(".")
			for candidate in (
				f"gofix.gofix_services.page.gofix_ops_hub.{module_name}",
				f"gofix.gofix_services.{module_name}",
			):
				try:
					module = importlib.import_module(candidate)
				except ModuleNotFoundError:
					continue
				if getattr(module, attr, None):
					break
			else:
				self.fail(f"{legacy} points at {replacement}, which does not exist")

	def test_superseded_order_endpoint_refuses_without_an_order(self):
		with patch.object(api.frappe.db, "exists", return_value=False):
			with self.assertRaises(frappe.ValidationError) as caught:
				api._get_scoped_service_order("SRGF-does-not-exist", "write")
		self.assertIn("Service Request is the operational document", str(caught.exception))

	def test_job_assignment_creation_denies_before_loading_service_request(self):
		with (
			patch(
				"frappe.has_permission",
				side_effect=frappe.PermissionError("denied"),
			),
			patch.object(job_assignment, "assert_service_request_access") as scope_guard,
		):
			with self.assertRaises(frappe.PermissionError):
				job_assignment.authorize_job_assignment_creation("SR-1", "EMP-1")
		scope_guard.assert_not_called()

	def test_tablet_config_defines_a_bounded_query_limit(self):
		"""The guest tablet endpoint must cap what a caller can ask it to read.

		Asserted on behaviour rather than on a source line: this endpoint moved
		from gofix to ch_pos in the token consolidation and its local names
		changed with it, which silently broke this check for months.
		"""
		source = inspect.getsource(token_api.get_tablet_config)
		self.assertRegex(
			source,
			r'min\(\s*\w+\(\s*"token_queue_limit",\s*\d+\s*\),\s*2000\s*\)',
			"the tablet queue limit is no longer capped at 2000",
		)
		self.assertNotIn(
			"limit_page_length=None", source,
			"an unbounded read reached a guest endpoint",
		)
		self.assertIn("limit_page_length=", source)

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

	def test_ops_hub_can_still_submit_what_it_creates(self):
		"""Every doctype the Ops Hub submits for the operator must grant submit.

		Custom DocPerm REPLACES a doctype's own permissions wholesale, so a row
		that says submit=0 silently overrides the submit=1 the DocType ships.
		Both Job Assignment and Spare Parts Usage were zeroed that way for every
		role including System Manager, and because Administrator bypasses
		DocPerms entirely the flow passed every admin test while assigning a
		technician was impossible for all 115 real users.
		"""
		from gofix.setup.permissions import MANAGER_TRANSACTION_GRANTS, _operational_docperm_specs

		specs = _operational_docperm_specs()
		for doctype, ptypes in MANAGER_TRANSACTION_GRANTS.items():
			if "submit" not in ptypes or not frappe.db.exists("DocType", doctype):
				continue
			roles = [r for r in specs.get(doctype, {}) if frappe.db.exists("Role", r)]
			if not roles:
				continue
			# Custom DocPerm wins outright where it exists; fall back to DocPerm.
			table = "Custom DocPerm" if frappe.db.exists(
				"Custom DocPerm", {"parent": doctype}) else "DocPerm"
			granted = frappe.get_all(
				table,
				filters={"parent": doctype, "role": ["in", roles], "permlevel": 0, "submit": 1},
				pluck="role",
			)
			self.assertTrue(
				granted,
				f"No configured role can submit {doctype} ({table}); the Ops Hub "
				f"submits it on behalf of {sorted(roles)} and would raise "
				f"PermissionError for every non-Administrator user.",
			)

	def test_blank_by_design_links_do_not_revoke_write(self):
		"""A link that is legitimately empty must not deny write on its parent.

		Frappe ANDs a User Permission match for every link field on the header
		AND on every child row. A CH User Scope issues an Employee and per-store
		Warehouse User Permissions, and a blank reads as "not one of yours" — so
		appending an issue line for a customer-reported fault (no technician, by
		design) revoked the writer's permission on the ticket itself.
		"""
		from gofix.setup.permissions import _UNGOVERNED_LINK_FIELDS

		for doctype, fieldnames in _UNGOVERNED_LINK_FIELDS.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			meta = frappe.get_meta(doctype)
			for fieldname in fieldnames:
				df = meta.get_field(fieldname)
				if not df or df.fieldtype != "Link":
					continue
				self.assertTrue(
					df.ignore_user_permissions,
					f"{doctype}.{fieldname} is blank by design but still governed "
					f"by User Permissions — a blank value denies write on the "
					f"whole repair ticket.",
				)
