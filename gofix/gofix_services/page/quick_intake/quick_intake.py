# Copyright (c) 2026, GoFix and contributors
# Quick Intake — Backend for POS-style rapid walk-in registration

import frappe
from frappe import _
from frappe.utils import today, flt


@frappe.whitelist()
def get_intake_context():
	"""Return context for the intake form: warehouses, recent customers, config."""
	user_warehouse = frappe.defaults.get_user_default("warehouse") or ""
	company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value("Global Defaults", "default_company")

	warehouses = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0, "disabled": 0},
		pluck="name",
		order_by="name",
	)

	# Recent customers (last 50 unique)
	recent = frappe.db.sql("""
		SELECT DISTINCT customer, customer_name, contact_number
		FROM `tabService Request`
		WHERE company = %s
		ORDER BY creation DESC
		LIMIT 50
	""", (company,), as_dict=True)

	return {
		"default_warehouse": user_warehouse,
		"company": company,
		"warehouses": warehouses,
		"recent_customers": recent,
	}


@frappe.whitelist()
def search_serial(serial_no):
	"""Look up serial and return device details + warranty + open SRs."""
	if not frappe.db.exists("Serial No", serial_no):
		return {"found": False}

	sn = frappe.get_doc("Serial No", serial_no)

	# Check warranty via ch_item_master
	warranty_info = {"warranty_covered": False, "warranty_status": "No Warranty"}
	try:
		from ch_item_master.ch_item_master.warranty_api import check_warranty
		warranty_info = check_warranty(serial_no=serial_no, company=sn.company)
	except Exception:
		pass

	# Open service requests for this serial
	open_srs = frappe.get_all("Service Request", filters={
		"serial_no": serial_no,
		"status": ["not in", ["Completed", "Delivered", "Cancelled", "Invoiced"]],
		"docstatus": ["<", 2],
	}, fields=["name", "status", "service_date", "issue_category"], limit=5)

	return {
		"found": True,
		"item_code": sn.item_code,
		"item_name": sn.item_name,
		"brand": frappe.db.get_value("Item", sn.item_code, "brand") or "",
		"warranty_status": "Under Warranty" if warranty_info.get("warranty_covered") else "Out of Warranty",
		"warranty_plan": (warranty_info.get("covering_plan") or {}).get("warranty_plan", ""),
		"warranty_expiry": str((warranty_info.get("covering_plan") or {}).get("end_date", "")),
		"open_requests": open_srs,
	}


@frappe.whitelist()
def search_customer(query):
	"""Find customers by phone or name fragment."""
	if not query or len(query) < 3:
		return []

	results = frappe.db.sql("""
		SELECT DISTINCT sr.customer, sr.customer_name, sr.contact_number, sr.email
		FROM `tabService Request` sr
		WHERE (sr.customer_name LIKE %(q)s
			OR sr.contact_number LIKE %(q)s
			OR sr.customer LIKE %(q)s)
		ORDER BY sr.creation DESC
		LIMIT 15
	""", {"q": f"%{query}%"}, as_dict=True)

	if not results:
		# Fallback: search Customer doctype
		results = frappe.get_all("Customer", filters={
			"customer_name": ["like", f"%{query}%"],
			"disabled": 0,
		}, fields=["name as customer", "customer_name"], limit=10)

	return results


@frappe.whitelist()
def submit_intake(data):
	"""Create a Service Request from quick intake data.

	data: dict with keys — customer, customer_name, contact_number, email,
	      serial_no, device_item, brand, issue_category, issue_description,
	      product_condition_desc, accessories_received, backup_info,
	      password, pattern, source_warehouse, company, priority
	"""
	import json
	if isinstance(data, str):
		data = json.loads(data)

	required = ["customer", "contact_number", "device_item", "issue_description", "source_warehouse"]
	for field in required:
		if not data.get(field):
			frappe.throw(_("{0} is required").format(field))

	sr = frappe.new_doc("Service Request")
	sr.company = data.get("company") or frappe.defaults.get_user_default("company")
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

	return {
		"name": sr.name,
		"message": _("Service Request {0} created successfully").format(sr.name),
	}
