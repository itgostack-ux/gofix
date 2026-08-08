# Copyright (c) 2026, GoFix and contributors
# Quick Intake — Backend for POS-style rapid walk-in registration

import re
import frappe
from frappe import _
from frappe.utils import today, flt

from gofix.config import get_int_setting, require_role_setting
from gofix.gofix_services.store_context import active_company, build_store_context

_GSTIN_RE = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$')
_QUICK_INTAKE_ROLES = (
	"Service Manager",
	"Service Engineer",
	"Service User",
	"Store Manager",
	"Store Executive",
	"GoFix Floor Manager",
)


def _require_intake_access(*read_doctypes):
	require_role_setting("app_access_roles", _QUICK_INTAKE_ROLES, action=_("use quick intake"))
	for doctype in read_doctypes:
		if not frappe.has_permission(doctype, ptype="read"):
			frappe.throw(
				_("You do not have read permission for {0}.").format(doctype),
				frappe.PermissionError,
			)


@frappe.whitelist()
def get_intake_context(company=None) -> dict:
	"""Return context for the intake form: warehouses, recent customers, config."""
	_require_intake_access("Service Request", "Customer", "Warehouse", "CH Store")
	from gofix.scope_guard import user_scope
	allowed_wh, _allowed_co, bypass = user_scope()

	company = active_company(company)
	ctx = build_store_context(company=company, prefer_first=True)
	user_warehouse = ctx["default_warehouse"]
	warehouses = ctx["warehouses"]

	# Store scope: a technician only sees warehouses — and the recent-customer
	# history — for the stores they are entitled to. Fail closed when scoped
	# with no allowed warehouses.
	if not bypass:
		warehouses = [w for w in warehouses if w in allowed_wh]
		if user_warehouse and user_warehouse not in allowed_wh:
			user_warehouse = warehouses[0] if warehouses else ""

	# Recent customers (last 50 unique) — restricted to in-scope warehouses.
	if bypass:
		recent = frappe.db.sql("""
			SELECT DISTINCT customer, customer_name, contact_number
			FROM `tabService Request`
			WHERE company = %(company)s
			ORDER BY creation DESC
			LIMIT 50
		""", {"company": company}, as_dict=True)
	elif warehouses:
		recent = frappe.db.sql("""
			SELECT DISTINCT customer, customer_name, contact_number
			FROM `tabService Request`
			WHERE company = %(company)s AND source_warehouse IN %(wh)s
			ORDER BY creation DESC
			LIMIT 50
		""", {"company": company, "wh": tuple(warehouses)}, as_dict=True)
	else:
		recent = []

	return {
		"default_warehouse": user_warehouse,
		"company": company,
		"stores": ctx["stores"],
		"warehouses": warehouses,
		"recent_customers": recent,
	}


@frappe.whitelist()
def search_serial(serial_no) -> dict:
	"""Look up serial and return device details + warranty + open SRs."""
	_require_intake_access("Serial No", "Item", "Service Request")
	serial_no = (serial_no or "").strip()
	if not serial_no or len(serial_no) > 140 or not frappe.db.exists("Serial No", serial_no):
		return {"found": False}

	sn = frappe.get_doc("Serial No", serial_no)
	sn.check_permission("read")
	from gofix.scope_guard import assert_warehouse
	assert_warehouse(
		warehouse=sn.warehouse,
		company=sn.company,
		msg=_("This serial number is outside your assigned store scope."),
	)

	# Check warranty via ch_item_master
	warranty_info = {"warranty_covered": False, "warranty_status": "No Warranty"}
	try:
		from ch_item_master.ch_item_master.warranty_api import check_warranty
		warranty_info = check_warranty(serial_no=serial_no, company=sn.company)
	except Exception:
		pass

	# Open service requests for this serial — scoped to the caller's stores so
	# another store's service history for the device is not exposed.
	from gofix.scope_guard import user_scope
	allowed_wh, _co, bypass = user_scope()
	sr_filters = {
		"serial_no": serial_no,
		"decision": ["not in", ["Completed", "Delivered", "Cancelled", "Invoiced"]],
		"docstatus": ["<", 2],
	}
	if not bypass:
		sr_filters["source_warehouse"] = ["in", list(allowed_wh) or ["__none__"]]
	open_srs = frappe.get_all("Service Request", filters=sr_filters,
		fields=["name", "decision", "service_date", "issue_category"], limit=5)
	for row in open_srs:
		row["status"] = row.decision

	return {
		"found": True,
		"item_code": sn.item_code,
		"item_name": sn.item_name,
		"brand": _get_brand(sn.item_code),
		"warranty_status": "Under Warranty" if warranty_info.get("warranty_covered") else "Out of Warranty",
		"warranty_plan": (warranty_info.get("covering_plan") or {}).get("warranty_plan", ""),
		"warranty_expiry": str((warranty_info.get("covering_plan") or {}).get("end_date", "")),
		"open_requests": open_srs,
	}


@frappe.whitelist()
def search_customer(query) -> list:
	"""Find customers by phone or name fragment."""
	_require_intake_access("Customer", "Service Request")
	query = (query or "").strip()
	if len(query) < 3 or len(query) > 140:
		return []

	# Restrict the service-history search to the caller's in-scope stores so a
	# technician cannot enumerate customer contact details from other stores.
	from gofix.scope_guard import user_scope
	allowed_wh, _co, bypass = user_scope()
	if bypass:
		results = frappe.db.sql("""
			SELECT DISTINCT sr.customer, sr.customer_name, sr.contact_number, sr.email
			FROM `tabService Request` sr
			WHERE (sr.customer_name LIKE %(q)s
				OR sr.contact_number LIKE %(q)s
				OR sr.customer LIKE %(q)s)
			ORDER BY sr.creation DESC
			LIMIT 15
		""", {"q": f"%{query}%"}, as_dict=True)
	elif allowed_wh:
		results = frappe.db.sql("""
			SELECT DISTINCT sr.customer, sr.customer_name, sr.contact_number, sr.email
			FROM `tabService Request` sr
			WHERE (sr.customer_name LIKE %(q)s
				OR sr.contact_number LIKE %(q)s
				OR sr.customer LIKE %(q)s)
				AND sr.source_warehouse IN %(wh)s
			ORDER BY sr.creation DESC
			LIMIT 15
		""", {"q": f"%{query}%", "wh": tuple(allowed_wh)}, as_dict=True)
	else:
		results = []

	if not results and bypass:
		# Fallback: search Customer doctype
		results = frappe.get_all("Customer", filters={
			"customer_name": ["like", f"%{query}%"],
			"disabled": 0,
		}, fields=["name as customer", "customer_name"], limit=10)

	return results


def _get_brand(item_code: str) -> str:
	"""Return brand for an item, falling back to the variant template's brand."""
	brand = frappe.db.get_value("Item", item_code, "brand") or ""
	if not brand:
		template = frappe.db.get_value("Item", item_code, "variant_of")
		if template:
			brand = frappe.db.get_value("Item", template, "brand") or ""
	return brand


@frappe.whitelist()
def get_customer_classification(customer: str) -> dict:
	"""Return customer_type (B2B/B2C) and visit_type (New/Regular/VIP) for display.

	Mirrors the logic in ServiceRequest.detect_customer_type() /
	detect_visit_type() so the POS operator sees the same classification
	before the SR is saved.
	"""
	_require_intake_access("Customer", "Service Request")
	customer_doc = frappe.get_doc("Customer", customer)
	customer_doc.check_permission("read")
	gstin = customer_doc.gstin or ""
	customer_type = "B2B" if _GSTIN_RE.match(gstin.strip().upper()) else "B2C"

	from gofix.scope_guard import user_scope
	allowed_wh, _allowed_co, bypass = user_scope()
	filters = {"customer": customer, "docstatus": ["<", 2]}
	if not bypass:
		if not allowed_wh:
			frappe.throw(_("No service-store scope is assigned to your user."), frappe.PermissionError)
		filters["source_warehouse"] = ["in", sorted(allowed_wh)]
	prior_count = frappe.db.count("Service Request", filters=filters)
	vip_threshold = get_int_setting("vip_customer_request_threshold", 10, minimum=1)

	if prior_count == 0:
		visit_type = "New"
	elif prior_count < vip_threshold:
		visit_type = "Regular"
	else:
		visit_type = "VIP"

	return {
		"customer_type": customer_type,
		"visit_type": visit_type,
		"prior_count": prior_count,
		"gstin": gstin,
	}


@frappe.whitelist()
def get_token_intake_defaults(token_name: str) -> dict:
	"""Prefill payload for a job card raised from a GoFix queue token.

	Returns the customer/device/symptom details captured on the self check-in
	tablet plus the Issue Category the first mapped symptom points at
	(GoFix Symptom.backend_category), so the Service Request lands in the same
	service taxonomy the token reports use.
	"""
	from gofix.api.token_api import _assert_token_scope, _ensure_fde

	_ensure_fde()
	_require_intake_access("GoFix Token")
	if not token_name or not frappe.db.exists("GoFix Token", token_name):
		frappe.throw(_("GoFix Token {0} not found.").format(token_name or ""))
	token = frappe.get_doc("GoFix Token", token_name)
	token.check_permission("read")
	_assert_token_scope(token)

	# Canonical item-master Brand behind the customer-facing label (e.g.
	# "Google Pixel" → "Google") so the job card and Item analytics agree.
	canonical_brand = None
	if token.device_type and token.device_brand:
		canonical_brand = frappe.db.get_value(
			"GoFix Brand Option", f"{token.device_type}::{token.device_brand}", "brand"
		)

	return {
		"token": token.name,
		"token_number": token.token_number,
		"status": token.status,
		"customer_name": token.customer_name,
		"customer_phone": token.customer_phone,
		"visit_reason": token.visit_reason,
		"device_type": token.device_type,
		"device_brand": token.device_brand,
		"canonical_brand": canonical_brand or token.device_brand,
		"device_model": token.device_model,
		"other_device_hint": token.other_device_hint,
		"symptoms": [r.symptom_name for r in (token.selected_issues or [])],
		"additional_notes": token.additional_notes,
		"issue_category": _resolve_backend_category(token),
		"store": token.store,
		"store_name": token.store_name,
	}


def _resolve_backend_category(token) -> str:
	"""First mapped Issue Category across the token's selected symptoms."""
	for row in token.selected_issues or []:
		category = None
		if row.symptom_ref:
			category = frappe.db.get_value("GoFix Symptom", row.symptom_ref, "backend_category")
		if not category and row.device_type and row.symptom_name:
			category = frappe.db.get_value(
				"GoFix Symptom", f"{row.device_type}::{row.symptom_name}", "backend_category"
			)
		if category:
			return category
	return ""


def _link_gofix_token(sr, token_name: str) -> None:
	"""Bind a freshly created Service Request back to its queue token.

	Walks the token through its allowed transitions to "Job Card Created" so
	the FDE queue and the token-to-job-card conversion reports stay accurate.
	Raises inside the intake transaction — if linking fails, the Service
	Request rolls back with it rather than leaving an orphan job card.
	"""
	from gofix.gofix_services.doctype.gofix_token.gofix_token import (
		STATUS_ATTENDING,
		STATUS_CALLED,
		STATUS_JOB_CARD,
		STATUS_WAITING,
		TERMINAL_STATUSES,
	)

	if not frappe.db.exists("GoFix Token", token_name):
		frappe.throw(_("GoFix Token {0} not found.").format(token_name))
	token = frappe.get_doc("GoFix Token", token_name)
	token.check_permission("write")
	label = token.token_number or token_name
	if token.service_request and token.service_request != sr.name:
		frappe.throw(
			_("Token {0} is already linked to Service Request {1}.").format(label, token.service_request)
		)
	if token.status in TERMINAL_STATUSES:
		frappe.throw(_("Token {0} is already closed ({1}).").format(label, token.status))
	if token.company and token.company != sr.company:
		frappe.throw(
			_("Token {0} belongs to {1} and cannot be linked to a {2} job card.").format(
				label, token.company, sr.company
			)
		)
	if token.store and token.store != sr.source_warehouse:
		frappe.throw(
			_("Token {0} belongs to another store and cannot be linked to this job card.").format(label),
			frappe.PermissionError,
		)
	if token.status in {STATUS_WAITING, STATUS_CALLED}:
		token.status = STATUS_ATTENDING
		token.save()
	token.service_request = sr.name
	if token.status == STATUS_ATTENDING:
		token.status = STATUS_JOB_CARD
	token.save()


@frappe.whitelist(methods=["POST"])
def submit_intake(data) -> dict:
	"""Create a Service Request from quick intake data.

	data: dict with keys — customer, customer_name, contact_number, email,
	      serial_no, device_item, brand, issue_category, issue_description,
	      product_condition_desc, accessories_received, backup_info,
	      password, pattern, source_warehouse, company, priority
	"""
	import json
	if isinstance(data, str):
		data = json.loads(data)
	require_role_setting("app_access_roles", _QUICK_INTAKE_ROLES, action=_("create quick intake"))
	for doctype, permission_type in (
		("Service Request", "create"),
		("Service Request", "submit"),
		("Customer", "read"),
		("Item", "read"),
	):
		if not frappe.has_permission(doctype, ptype=permission_type):
			frappe.throw(
				_("You do not have {0} permission for {1}.").format(permission_type, doctype),
				frappe.PermissionError,
			)

	required = ["customer", "contact_number", "device_item", "issue_description", "source_warehouse"]
	for field in required:
		if not data.get(field):
			frappe.throw(_("{0} is required").format(field), title=_("Validation Error"))

	# Bind the intake to a warehouse the caller is entitled to — a technician
	# must not file a Service Request against another store's warehouse.
	from gofix.scope_guard import assert_warehouse
	warehouse_company = frappe.db.get_value(
		"Warehouse", data["source_warehouse"], ["company", "disabled"], as_dict=True
	)
	if not warehouse_company or warehouse_company.disabled:
		frappe.throw(_("The intake warehouse is missing or disabled."), frappe.ValidationError)
	if data.get("company") and data["company"] != warehouse_company.company:
		frappe.throw(_("The intake company does not match the warehouse."), frappe.ValidationError)
	assert_warehouse(
		warehouse=data["source_warehouse"],
		company=warehouse_company.company,
		msg=_("You are not entitled to create intake at this warehouse."),
	)

	sr = frappe.new_doc("Service Request")
	sr.company = warehouse_company.company
	sr.customer = data["customer"]
	sr.customer_name = data.get("customer_name") or frappe.db.get_value("Customer", data["customer"], "customer_name")
	sr.contact_number = data["contact_number"]
	sr.email = data.get("email", "")
	sr.serial_no = data.get("serial_no", "")
	sr.device_item = data["device_item"]
	sr.device_item_name = data.get("device_item_name") or frappe.db.get_value("Item", data["device_item"], "item_name")
	sr.brand = data.get("brand", "")
	sr.issue_category = data.get("issue_category", "")
	sr.issue_description = data["issue_description"]
	sr.product_condition_desc = data.get("product_condition_desc", "")
	sr.accessories_received = data.get("accessories_received", "")
	sr.backup_info = data.get("backup_info", "")
	sr.password = data.get("password", "")
	sr.pattern = data.get("pattern", "")
	sr.source_warehouse = data["source_warehouse"]
	sr.priority = data.get("priority", "Medium")
	sr.service_date = today()

	sr.insert()
	sr.submit()

	# Queue-token handoff: bind the token so the FDE queue flips to
	# "Job Card Created" and walk-in conversion reporting stays intact.
	if data.get("gofix_token"):
		_link_gofix_token(sr, data["gofix_token"])

	return {
		"name": sr.name,
		"message": _("Service Request {0} created successfully").format(sr.name),
	}
