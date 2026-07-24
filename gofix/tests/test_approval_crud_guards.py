import json
from pathlib import Path
from unittest import TestCase

import frappe

from gofix import tracking
from gofix.gofix_services.doctype.service_request.service_request import ServiceRequest
from gofix.gofix_services.doctype.spare_parts_usage.spare_parts_usage import SparePartsUsage
from gofix.overrides.sales_order import CustomSalesOrder


class FakeDocument:
	def __init__(self, values, before=None, is_new=False):
		self._before = frappe._dict(before) if before is not None else None
		self._is_new = is_new
		for key, value in values.items():
			setattr(self, key, value)

	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def is_new(self):
		return self._is_new

	def get_doc_before_save(self):
		return self._before


class TestApprovalCrudGuards(TestCase):
	def test_service_request_rejects_forged_discount_approver(self):
		doc = FakeDocument(
			{
				"discount_approved_by": "forged@example.com",
				"discount_exception_request": None,
				"substitution_approved_by": None,
				"substitution_exception_request": None,
			},
			is_new=True,
		)
		doc._APPROVAL_EVIDENCE_FIELDS = ServiceRequest._APPROVAL_EVIDENCE_FIELDS
		with self.assertRaises(frappe.PermissionError):
			ServiceRequest._validate_approval_evidence(doc)

	def test_spare_usage_rejects_forged_approval(self):
		values = {fieldname: None for fieldname in SparePartsUsage._APPROVAL_FIELDS}
		values.update({"requires_approval": 1, "approval_status": "Approved"})
		doc = FakeDocument(values, is_new=True)
		doc._APPROVAL_FIELDS = SparePartsUsage._APPROVAL_FIELDS
		doc._has_approval_context = lambda: False
		with self.assertRaises(frappe.PermissionError):
			SparePartsUsage._validate_approval_evidence(doc)

	def test_sales_order_rejects_forged_delivery_verification(self):
		values = {fieldname: None for fieldname in CustomSalesOrder._SERVER_EVIDENCE_FIELDS}
		values["delivery_otp_verified"] = 1
		doc = FakeDocument(values, is_new=True)
		doc._SERVER_EVIDENCE_FIELDS = CustomSalesOrder._SERVER_EVIDENCE_FIELDS
		with self.assertRaises(frappe.PermissionError):
			CustomSalesOrder._validate_server_evidence(doc)

	def test_tracking_token_storage_is_one_way(self):
		raw = "8fbcf413-3db5-4961-acd1-99c84386f2cf"
		digest = tracking.tracking_token_digest(raw)
		self.assertTrue(digest.startswith("sha256:"))
		self.assertNotIn(raw, digest)

	def test_service_request_secret_fields_are_privileged(self):
		path = Path(__file__).parents[1] / "gofix_services/doctype/service_request/service_request.json"
		definition = json.loads(path.read_text())
		fields = {field["fieldname"]: field for field in definition["fields"]}
		for fieldname in (
			"tracking_token",
			"discount_approved_by",
			"discount_exception_request",
			"substitution_approved_by",
			"substitution_exception_request",
		):
			self.assertEqual(fields[fieldname].get("permlevel"), 1)
			self.assertEqual(fields[fieldname].get("read_only"), 1)
		self.assertTrue(any(
			permission.get("role") == "System Manager"
			and permission.get("permlevel") == 1
			and permission.get("read")
			for permission in definition["permissions"]
		))
