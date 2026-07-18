import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from gofix.gofix_services import customer_address


class TestCustomerAddressImport(unittest.TestCase):
	def test_customer_hook_defers_sync_during_data_import(self):
		doc = SimpleNamespace(name="CUST-IMPORT-0001")
		previous_in_import = customer_address.frappe.flags.in_import

		try:
			customer_address.frappe.flags.in_import = True
			with patch.object(customer_address, "sync_customer_address") as sync_customer:
				customer_address.sync_standard_customer_address(doc)
		finally:
			customer_address.frappe.flags.in_import = previous_in_import

		sync_customer.assert_not_called()

	def test_customer_hook_syncs_normal_saves(self):
		doc = SimpleNamespace(name="CUST-0001")
		previous_in_import = customer_address.frappe.flags.in_import

		try:
			customer_address.frappe.flags.in_import = False
			with patch.object(customer_address, "sync_customer_address") as sync_customer:
				customer_address.sync_standard_customer_address(doc)
		finally:
			customer_address.frappe.flags.in_import = previous_in_import

		sync_customer.assert_called_once_with("CUST-0001")

	def test_import_finalizer_waits_for_all_logs(self):
		doc = SimpleNamespace(
			name="DATA-IMPORT-0001",
			reference_doctype="Customer",
			status="Partial Success",
			payload_count=2,
		)

		with (
			patch.object(customer_address, "_data_import_is_complete", return_value=False),
			patch.object(customer_address, "sync_customer_addresses") as bulk_sync,
		):
			customer_address.on_data_import_change(doc)

		bulk_sync.assert_not_called()

	def test_import_finalizer_reconciles_distinct_successful_customers(self):
		doc = SimpleNamespace(
			name="DATA-IMPORT-0002",
			reference_doctype="Customer",
			status="Success",
			payload_count=3,
		)

		with (
			patch.object(customer_address, "_data_import_is_complete", return_value=True),
			patch.object(
				customer_address.frappe,
				"get_all",
				return_value=["CUST-0001", "CUST-0001", None, "CUST-0002"],
			) as get_all,
			patch.object(customer_address, "sync_customer_addresses") as bulk_sync,
		):
			customer_address.on_data_import_change(doc)

		get_all.assert_called_once_with(
			"Data Import Log",
			filters={"data_import": "DATA-IMPORT-0002", "success": 1},
			pluck="docname",
		)
		bulk_sync.assert_called_once_with(
			customer_names=["CUST-0001", "CUST-0002"],
			commit=True,
		)


class TestCustomerAddressImportIntegration(IntegrationTestCase):
	def test_imported_customer_is_reconciled_after_insert(self):
		savepoint = "test_customer_address_import_reconciliation"
		previous_in_import = frappe.flags.in_import
		customer_name = None
		frappe.db.savepoint(save_point=savepoint)

		try:
			frappe.flags.in_import = True
			customer = frappe.new_doc("Customer")
			customer.customer_name = f"Import Address Test {uuid.uuid4().hex[:8]}"
			customer.customer_type = "Individual"
			customer.customer_group = frappe.db.get_value(
				"Customer Group", {"is_group": 0}, "name"
			)
			customer.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
			city = frappe.db.get_value("CH City", {}, "name")
			customer.append(
				"billing_addresses",
				{
					"address_title": "Imported Billing",
					"address_type": "Billing",
					"is_active": 1,
					"address_line1": "1 Import Test Road",
					"city": city,
					"country": "India",
				},
			)
			customer.insert(ignore_permissions=True)
			customer_name = customer.name

			self.assertFalse(
				frappe.db.exists(
					"Dynamic Link",
					{
						"parenttype": "Address",
						"link_doctype": "Customer",
						"link_name": customer.name,
					},
				)
			)
			self.assertFalse(
				frappe.db.get_value("Customer", customer.name, "customer_primary_address")
			)

			frappe.flags.in_import = False
			self.assertEqual(customer_address.sync_customer_address(customer.name), "created")

			address_name = frappe.db.get_value(
				"Customer", customer.name, "customer_primary_address"
			)
			self.assertTrue(address_name)
			self.assertTrue(
				frappe.db.exists(
					"Dynamic Link",
					{
						"parenttype": "Address",
						"parent": address_name,
						"link_doctype": "Customer",
						"link_name": customer.name,
					},
				)
			)
		finally:
			frappe.flags.in_import = previous_in_import
			frappe.db.rollback(save_point=savepoint)
			if customer_name:
				frappe.clear_document_cache("Customer", customer_name)


if __name__ == "__main__":
	unittest.main()
