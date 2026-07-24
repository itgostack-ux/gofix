from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from gofix import security
from gofix.gofix_services import technician_intelligence
from gofix.gofix_services.doctype.service_request import service_request
from gofix.gofix_services.page.gofix_ops_hub import gofix_ops_hub


class TestFinalScopeHardening(TestCase):
	def test_manager_access_uses_only_the_configured_role_setting(self):
		with (
			patch.object(security, "get_role_setting", return_value={"Service Manager"}) as setting,
			patch.object(security, "_get_roles", return_value={"Service Viewer"}),
			patch.object(security, "is_privileged_user", return_value=False),
		):
			self.assertFalse(security._is_service_manager("viewer@example.com"))
		setting.assert_called_once_with("service_manager_roles")

		context_source = inspect.getsource(gofix_ops_hub.get_ops_context)
		self.assertIn('has_role_setting("service_manager_roles"', context_source)
		self.assertNotIn("Service Viewer", context_source)

	def test_open_request_lookup_is_customer_company_scoped_and_bounded(self):
		doc = frappe._dict({
			"name": "SR-1",
			"customer": "CUST-1",
			"company": "Company A",
			"source_warehouse": "Store A",
		})
		customer = Mock()
		with (
			patch.object(service_request, "_require_service_lookup_access"),
			patch.object(service_request, "assert_service_request_access", return_value=doc),
			patch.object(service_request.frappe, "get_doc", return_value=customer),
			patch.object(service_request, "get_int_setting", return_value=25),
			patch.object(service_request.frappe, "get_list", return_value=[]) as get_list,
		):
			self.assertEqual(service_request._get_scoped_open_requests("SR-1"), [])

		customer.check_permission.assert_called_once_with("read")
		kwargs = get_list.call_args.kwargs
		self.assertEqual(kwargs["filters"]["customer"], "CUST-1")
		self.assertEqual(kwargs["filters"]["company"], "Company A")
		self.assertEqual(kwargs["limit_page_length"], 25)

	def test_device_lookup_is_bound_to_the_ticket_customer_and_limit(self):
		doc = frappe._dict({
			"name": "SR-1",
			"customer": "CUST-1",
			"device_item": "ITEM-1",
			"company": "Company A",
			"source_warehouse": "Store A",
		})
		customer = Mock()
		item = Mock(item_name="Phone", brand="Brand")

		def get_doc(doctype, name):
			return customer if doctype == "Customer" else item

		with (
			patch.object(service_request, "_require_service_lookup_access"),
			patch.object(service_request, "assert_service_request_access", return_value=doc),
			patch.object(service_request.frappe, "get_doc", side_effect=get_doc),
			patch.object(service_request.frappe, "has_permission", return_value=True),
			patch.object(service_request, "get_int_setting", return_value=100),
			patch.object(service_request.frappe, "get_list", return_value=[]) as get_list,
		):
			self.assertEqual(service_request._get_scoped_device_details("SR-1"), [])

		customer.check_permission.assert_called_once_with("read")
		item.check_permission.assert_called_once_with("read")
		kwargs = get_list.call_args.kwargs
		self.assertEqual(kwargs["filters"]["customer"], "CUST-1")
		self.assertEqual(kwargs["filters"]["item_code"], "ITEM-1")
		self.assertEqual(kwargs["limit_page_length"], 100)

	def test_billing_address_rejects_a_customer_not_bound_to_the_request(self):
		doc = frappe._dict({
			"name": "SR-1",
			"customer": "CUST-1",
			"company": "Company A",
			"source_warehouse": "Store A",
		})
		with (
			patch.object(service_request, "_require_service_lookup_access"),
			patch.object(service_request, "assert_service_request_access", return_value=doc),
			patch.object(service_request.frappe, "get_doc") as get_doc,
			self.assertRaises(frappe.PermissionError),
		):
			service_request.get_customer_billing_address(
				"CUST-2",
				service_request="SR-1",
				company="Company A",
				warehouse="Store A",
			)
		get_doc.assert_not_called()

	def test_new_request_billing_lookup_requires_exact_location_context(self):
		with (
			patch.object(service_request, "_require_service_lookup_access"),
			patch.object(service_request, "is_privileged_user", return_value=False),
			patch.object(service_request.frappe, "get_doc") as get_doc,
			self.assertRaises(frappe.PermissionError),
		):
			service_request.get_customer_billing_address("CUST-1")
		get_doc.assert_not_called()

	def test_recommendations_reject_mismatched_request_company_before_queries(self):
		doc = frappe._dict({
			"name": "SR-1",
			"company": "Company A",
			"source_warehouse": "Store A",
			"issue_category": "Screen",
		})
		with (
			patch.object(technician_intelligence, "require_role_setting"),
			patch.object(technician_intelligence.frappe, "has_permission", return_value=True),
			patch.object(technician_intelligence, "assert_service_request_access", return_value=doc),
			patch.object(technician_intelligence.frappe, "get_list") as get_list,
			self.assertRaises(frappe.PermissionError),
		):
			technician_intelligence.get_recommended_technicians(
				service_request="SR-1",
				company="Company B",
			)
		get_list.assert_not_called()

	def test_non_privileged_recommendations_require_a_ticket_or_location(self):
		with (
			patch.object(technician_intelligence, "require_role_setting"),
			patch.object(technician_intelligence.frappe, "has_permission", return_value=True),
			patch.object(technician_intelligence, "is_privileged_user", return_value=False),
			patch.object(technician_intelligence.frappe, "get_list") as get_list,
			self.assertRaises(frappe.PermissionError),
		):
			technician_intelligence.get_recommended_technicians(issue_category="Screen")
		get_list.assert_not_called()

	def test_technician_aggregates_are_parameterized_by_company_and_warehouse(self):
		with patch.object(technician_intelligence.frappe.db, "sql", return_value=[]) as sql:
			self.assertEqual(
				technician_intelligence._get_workload_map(
					("EMP-1",), company="Company A", warehouse="Store A"
				),
				{},
			)

		query, values = sql.call_args.args[:2]
		self.assertIn("sr.company = %(company)s", query)
		self.assertIn("sr.source_warehouse = %(warehouse)s", query)
		self.assertIn("sr.transferred_to_store = %(warehouse)s", query)
		self.assertEqual(values["company"], "Company A")
		self.assertEqual(values["warehouse"], "Store A")

	def test_lookup_limits_are_configured(self):
		settings_path = (
			Path(service_request.__file__).parents[2]
			/ "doctype/gofix_settings/gofix_settings.json"
		)
		settings = json.loads(settings_path.read_text())
		fieldnames = {field["fieldname"] for field in settings["fields"]}
		self.assertTrue({
			"service_history_limit",
			"device_serial_lookup_limit",
			"technician_candidate_limit",
			"technician_recommendation_limit",
		}.issubset(fieldnames))
