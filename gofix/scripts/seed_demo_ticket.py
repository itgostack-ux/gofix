"""Create one idempotent, end-to-end GoFix ticket for demonstrations.

Run with::

	bench --site <site> execute gofix.scripts.seed_demo_ticket.run

The records are deliberately labelled as demo data.  The script uses normal
document submission for the commercial documents, then adds a compact historic
timeline so every GoFix dashboard has a realistic completed case to display.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, add_to_date, flt, getdate, now_datetime


MARKER = "[GOFIX-DEMO-E2E]"
COMPANY = "GOFIX SOLUTIONS PRIVATE LIMITED"
STORE = "GF-ALWARTHIRUNAGAR-Sellable - GF"
SERVICE_ITEM = "GOFIX-REPAIR-SERVICE"
DEVICE_ITEM = "EXTERNAL-DEVICE"
DEMO_SERIAL = "GF-DEMO-IMEI-20260818"


def _set_existing(doc, values):
	for fieldname, value in values.items():
		if doc.meta.has_field(fieldname):
			doc.set(fieldname, value)


def _db_set_existing(doctype, name, values):
	meta = frappe.get_meta(doctype)
	values = {key: value for key, value in values.items() if meta.has_field(key)}
	if values:
		frappe.db.set_value(doctype, name, values, update_modified=False)


def _first_leaf(doctype, fallback=None):
	filters = {"is_group": 0} if frappe.get_meta(doctype).has_field("is_group") else {}
	return frappe.db.get_value(doctype, filters, "name", order_by="name") or fallback


def _ensure_customer():
	name = frappe.db.get_value("Customer", {"customer_name": "GoFix Demo Customer"}, "name")
	if name:
		return name
	doc = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": "GoFix Demo Customer",
		"customer_type": "Individual",
		"customer_group": _first_leaf("Customer Group", "Individual"),
		"territory": _first_leaf("Territory", "India"),
		"customer_details": f"{MARKER} Training/demo customer only.",
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_technician():
	name = frappe.db.get_value("Employee", {"employee_name": "GoFix Demo Technician"}, "name")
	if name:
		return name
	doc = frappe.get_doc({
		"doctype": "Employee",
		"first_name": "GoFix Demo",
		"last_name": "Technician",
		"employee_name": "GoFix Demo Technician",
		"company": COMPANY,
		"status": "Active",
		"gender": "Male",
		"date_of_birth": "1990-01-01",
		"date_of_joining": getdate(),
	})
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	return doc.name


def _find_existing():
	return frappe.db.get_value(
		"Service Request",
		{"serial_no": DEMO_SERIAL, "company": COMPANY, "docstatus": ("<", 2)},
		"name",
	)


def _create_service_request(customer, technician, started_at):
	doc = frappe.new_doc("Service Request")
	_set_existing(doc, {
		"customer": customer,
		"company": COMPANY,
		"contact_number": "9000000001",
		"email": "gofix.demo@example.com",
		"source_warehouse": STORE,
		"current_location": STORE,
		"state_name": "Tamil Nadu",
		"state_code": "33",
		"device_item": DEVICE_ITEM,
		"serial_no": DEMO_SERIAL,
		"actual_imei": DEMO_SERIAL,
		"brand": "DemoPhone",
		"device_condition": "Good",
		"product_condition_desc": "Minor cosmetic wear; screen and frame photographed at intake.",
		"accessories_received": "Protective case and SIM tray",
		"warranty_status": "Out of Warranty",
		"mode_of_service": "Walk-in",
		"backup_info": "Customer confirmed cloud backup; no local data handling required.",
		"data_backup_disclaimer": 1,
		"issue_category": "Charging & Power",
		"issue_description": "Device intermittently fails to charge and powers off under load.",
		"estimated_cost": 3500,
		"total_estimated_cost": 3500,
		"final_cost": 3500,
		"service_date": getdate(started_at),
		"received_datetime": started_at,
		"expected_completion_date": add_days(getdate(started_at), 1),
		"actual_completion_date": getdate(),
		"priority": "High",
		"decision": "Accepted",
		"repairability_status": "Repairable",
		"analysis_confirmed": 1,
		"customer_confirmed": 1,
		"confirmation_sent_at": add_to_date(started_at, hours=3),
		"service_engineer": technician,
		"repair_warranty_days": 90,
		"customer_remarks": "Customer approved the estimate and requested same-day completion.",
		"internal_remarks": f"{MARKER} Complete dashboard demonstration ticket.",
		"remarks": f"{MARKER} Demo only — not a real customer repair.",
	})
	doc.append("issue_lines", {
		"issue_category": "Charging & Power",
		"defect_code": "DEMO-CHG-PORT",
		"reported_by": "Technician",
		"description": "Charging connector contamination and loose contact confirmed.",
		"status": "Resolved",
	})
	doc.append("solution_lines", {
		"repair_solution": "Full Device Diagnosis",
		"issue_category": "General Diagnosis",
		"technician": technician,
		"status": "Completed",
		"technician_remarks": "Connector cleaned, contacts reseated, firmware diagnostics passed.",
	})
	# Full Device Diagnosis belongs to General Diagnosis; include its category in
	# the issue aggregate so the enforced issue→solution cascade remains valid.
	doc.append("issue_lines", {
		"issue_category": "General Diagnosis",
		"defect_code": "DEMO-DIAG",
		"reported_by": "Technician",
		"description": "End-to-end power, battery and charging diagnosis.",
		"status": "Resolved",
	})
	if doc.meta.has_field("estimate_versions"):
		doc.append("estimate_versions", {
			"version_number": 1,
			"estimate_amount": 3500,
			"labor_cost": 3500,
			"spare_cost": 0,
			"status": "Customer Approved",
			"created_by": "Administrator",
			"created_at": add_to_date(started_at, hours=2),
			"approved_by": "Administrator",
			"approved_at": add_to_date(started_at, hours=3),
			"customer_remarks": "Estimate approved for demo workflow.",
			"issues_snapshot": json.dumps(["Charging & Power", "General Diagnosis"]),
		})
	doc.flags.estimate_decision_override = True
	doc.insert(ignore_permissions=True)
	doc.submit()
	frappe.db.set_value("Service Request", doc.name, "creation", started_at, update_modified=False)
	return doc.name


def _create_service_order(service_request, customer):
	doc = frappe.get_doc({
		"doctype": "Sales Order",
		"customer": customer,
		"company": COMPANY,
		"transaction_date": getdate(),
		"delivery_date": add_days(getdate(), 1),
		"set_warehouse": STORE,
		"items": [{
			"item_code": SERVICE_ITEM,
			"qty": 1,
			"rate": 3500,
			"delivery_date": add_days(getdate(), 1),
		}],
	})
	_set_existing(doc, {
		"is_service_order": 1,
		"service_request": service_request,
		"device_model": "DemoPhone X1",
		"issue_category": "Charging & Power",
		"spare_parts_revenue": 0,
		"suggested_labor_cost": 3500,
		"suggested_total_cost": 3500,
		"price_override_amount": 0,
		"technician_damage_cost": 0,
		"rework_count": 0,
		"cost_bearer": "Customer",
		"warranty_status": "Out of Warranty",
		"repair_outcome": "Repaired",
		"spare_parts_cost": 0,
		"labor_cost": 2100,
		"total_repair_cost": 2100,
		"repair_margin": 1400,
		"repair_margin_pct": 40,
	})
	doc.insert(ignore_permissions=True)
	_db_set_existing("Service Request", service_request, {"service_order": doc.name})
	return doc.name


def _create_job_assignment(service_request, service_order, technician, started_at):
	doc = frappe.get_doc({
		"doctype": "Job Assignment",
		"service_request": service_request,
		"service_order": service_order,
		"assignment_date": getdate(started_at),
		"assignment_datetime": add_to_date(started_at, hours=5),
		"assigned_by": "Administrator",
		"assignment_type": "Technician Assignment",
		"job_type": "Repair",
		"service_engineer": technician,
		"assignment_status": "Open",
		"priority": "High",
		"estimated_hours": 3,
		"work_performed": "Diagnosed power path, cleaned and reseated charging connector, ran burn-in tests.",
		"technician_remarks": f"{MARKER} All functional tests passed.",
	})
	doc.insert(ignore_permissions=True)
	doc.submit()
	_db_set_existing("Job Assignment", doc.name, {
		"assignment_status": "Completed",
		"repair_outcome": "Repaired",
		"start_datetime": add_to_date(started_at, hours=6),
		"end_datetime": add_to_date(started_at, hours=10),
		"actual_hours": 4,
		"received_from_technician": 1,
		"received_date": getdate(),
		"received_datetime": add_to_date(started_at, hours=10),
	})
	return doc.name


def _add_qc(service_order, started_at):
	for idx, (check, critical) in enumerate((
		("Visual condition and intake evidence", 0),
		("Charging and power stability", 1),
		("Display and touch", 1),
		("Camera, speaker and microphone", 0),
		("Network, Wi-Fi and Bluetooth", 0),
		("Customer data safety", 1),
	), 1):
		row = frappe.new_doc("GoFix QC Checklist")
		row.update({
			"parent": service_order,
			"parenttype": "Sales Order",
			"parentfield": "qc_checklist",
			"idx": idx,
			"check_name": check,
			"result": "Pass",
			"is_mandatory": 1,
			"is_critical": critical,
			"remarks": "Verified during demo final QC.",
		})
		row.db_insert()
	_db_set_existing("Sales Order", service_order, {
		"qc_status": "Pass",
		"qc_checked_by": "Administrator",
		"qc_datetime": add_to_date(started_at, hours=12),
		"qc_pass_datetime": add_to_date(started_at, hours=12),
		"delivered_datetime": add_to_date(started_at, hours=15),
		"repair_outcome": "Repaired",
	})


def _create_invoice(service_order, service_request):
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	invoice = make_sales_invoice(service_order)
	invoice.posting_date = getdate()
	invoice.due_date = getdate()
	_set_existing(invoice, {
		"service_request": service_request,
		"service_order": service_order,
		"remarks": f"{MARKER} Demo repair invoice.",
	})
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	_db_set_existing("Service Request", service_request, {"service_invoice": invoice.name})
	return invoice.name


def _add_stage_history(service_request, started_at):
	stages = [
		("Draft", "Analysis", 1.0),
		("Analysis", "Customer Confirmation", 1.0),
		("Customer Confirmation", "Solution Assignment", 1.0),
		("Solution Assignment", "Technician Assignment", 2.0),
		("Technician Assignment", "Repair", 1.0),
		("Repair", "Quality Control", 4.0),
		("Quality Control", "Invoice", 2.0),
		("Invoice", "Delivered", 3.0),
	]
	for idx, (from_stage, to_stage, hours) in enumerate(stages, 1):
		row = frappe.new_doc("GoFix Status Log")
		row.update({
			"parent": service_request,
			"parenttype": "Service Request",
			"parentfield": "status_log",
			"idx": idx,
			"event_type": "Operations Stage",
			"from_status": from_stage,
			"to_status": to_stage,
			"changed_by": "Administrator",
			"changed_at": add_to_date(started_at, hours=sum(s[2] for s in stages[:idx])),
			"time_in_previous_status_hours": hours,
		})
		row.db_insert()


def _result(service_request):
	sr = frappe.db.get_value(
		"Service Request", service_request,
		["service_order", "service_invoice", "tracking_token_salt"], as_dict=True,
	)
	from gofix.tracking import derive_tracking_token
	return {
		"service_request": service_request,
		"service_order": sr.service_order,
		"sales_invoice": sr.service_invoice,
		"job_assignment": frappe.db.get_value("Job Assignment", {"service_request": service_request}, "name"),
		"customer": frappe.db.get_value("Service Request", service_request, "customer"),
		"serial_no": DEMO_SERIAL,
		"tracking_token": derive_tracking_token(service_request, sr.tracking_token_salt),
		"tracking_path": "/track-repair",
		"marker": MARKER,
	}


def _ensure_delivered(service_request):
	"""Bring an older seeded case to the final customer-delivery stage."""
	has_delivery = frappe.db.exists("GoFix Status Log", {
		"parent": service_request,
		"parenttype": "Service Request",
		"event_type": "Operations Stage",
		"to_status": "Delivered",
	})
	if not has_delivery:
		last = frappe.db.get_value(
			"GoFix Status Log",
			{"parent": service_request, "parenttype": "Service Request", "event_type": "Operations Stage"},
			["idx", "changed_at"],
			as_dict=True,
			order_by="idx desc",
		)
		row = frappe.new_doc("GoFix Status Log")
		row.update({
			"parent": service_request,
			"parenttype": "Service Request",
			"parentfield": "status_log",
			"idx": (last.idx if last else 0) + 1,
			"event_type": "Operations Stage",
			"from_status": "Invoice",
			"to_status": "Delivered",
			"changed_by": "Administrator",
			"changed_at": add_to_date(last.changed_at if last else now_datetime(), hours=3),
			"time_in_previous_status_hours": 3,
		})
		row.db_insert()
	_db_set_existing("Service Request", service_request, {
		"decision": "Delivered",
		"walkin_status": "Delivered",
	})


def _normalize_demo_timeline(service_request):
	"""Keep the demo's customer timeline chronological and stage-focused."""
	frappe.db.delete("GoFix Status Log", {
		"parent": service_request,
		"parenttype": "Service Request",
		"event_type": "Lifecycle",
	})
	rows = frappe.get_all(
		"GoFix Status Log",
		filters={"parent": service_request, "parenttype": "Service Request"},
		fields=["name"],
		order_by="changed_at asc, creation asc",
	)
	for idx, row in enumerate(rows, 1):
		frappe.db.set_value("GoFix Status Log", row.name, "idx", idx, update_modified=False)


def run():
	"""Create the demo graph once and return its document references."""
	frappe.set_user("Administrator")
	existing = _find_existing()
	if existing:
		_ensure_delivered(existing)
		_normalize_demo_timeline(existing)
		frappe.db.commit()
		return _result(existing)

	started_at = add_to_date(now_datetime(), hours=-18)
	try:
		customer = _ensure_customer()
		technician = _ensure_technician()
		service_request = _create_service_request(customer, technician, started_at)
		service_order = _create_service_order(service_request, customer)
		_create_job_assignment(service_request, service_order, technician, started_at)
		frappe.get_doc("Sales Order", service_order).submit()
		_add_qc(service_order, started_at)
		invoice = _create_invoice(service_order, service_request)
		_add_stage_history(service_request, started_at)
		_db_set_existing("Service Request", service_request, {
			"decision": "Delivered",
			"walkin_status": "Delivered",
			"actual_completion_date": getdate(),
			"service_invoice": invoice,
		})
		_normalize_demo_timeline(service_request)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		raise
	return _result(service_request)


def verify():
	"""Exercise the dashboard/report queries and prove the demo row is visible."""
	frappe.set_user("Administrator")
	service_request = _find_existing()
	if not service_request:
		frappe.throw("GoFix demo ticket is missing. Run seed_demo_ticket.run first.")
	refs = _result(service_request)
	filters = frappe._dict({"company": COMPANY})

	from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import get_ticket_queue
	from gofix.gofix_services.report.ceo_repair_dashboard.ceo_repair_dashboard import execute as ceo
	from gofix.gofix_services.report.device_service_history.device_service_history import execute as devices
	from gofix.gofix_services.report.gofix_ticket_stage_time.gofix_ticket_stage_time import execute as stages
	from gofix.gofix_services.report.gofix_ticket_status_by_location.gofix_ticket_status_by_location import execute as locations
	from gofix.gofix_services.report.repair_profitability.repair_profitability import execute as profitability
	from gofix.gofix_services.report.service_request_summary.service_request_summary import execute as summary
	from gofix.gofix_services.report.technician_performance.technician_performance import execute as technicians
	from gofix.tracking import _get_by_token

	checks = {}
	checks["ops_hub"] = get_ticket_queue(search=service_request, company=COMPANY)
	checks["service_request_summary"] = summary(filters)[1]
	checks["technician_performance"] = technicians(filters)[1]
	checks["ceo_repair_dashboard"] = ceo(filters)[1]
	checks["repair_profitability"] = profitability(filters)[1]
	checks["ticket_stage_time"] = stages(frappe._dict({"service_request": service_request}))[1]
	checks["ticket_status_by_location"] = locations(filters)[1]
	checks["device_service_history"] = devices(frappe._dict({"serial_no": DEMO_SERIAL, "company": COMPANY}))[1]
	tracking = _get_by_token(refs["tracking_token"])

	expected = {
		"ops_hub": any(row.get("name") == service_request for row in checks["ops_hub"]),
		"service_request_summary": any(row.get("status") == "Delivered" for row in checks["service_request_summary"]),
		"technician_performance": any("Demo Technician" in (row.get("technician") or "") for row in checks["technician_performance"]),
		"ceo_repair_dashboard": any(row.get("service_order") == refs["service_order"] for row in checks["ceo_repair_dashboard"]),
		"repair_profitability": any(row.get("name") == refs["service_order"] for row in checks["repair_profitability"]),
		"ticket_stage_time": any(row.get("ticket") == service_request for row in checks["ticket_stage_time"]),
		"ticket_status_by_location": any(row.get("name") == service_request for row in checks["ticket_status_by_location"]),
		"device_service_history": any(row.get("serial_no") == DEMO_SERIAL for row in checks["device_service_history"]),
		"customer_tracking": bool(tracking and tracking.get("name") == service_request),
		"submitted_invoice": frappe.db.get_value("Sales Invoice", refs["sales_invoice"], "docstatus") == 1,
	}
	failed = [name for name, passed in expected.items() if not passed]
	if failed:
		frappe.throw("GoFix demo verification failed: " + ", ".join(failed))
	return {
		"ok": True,
		"service_request": service_request,
		"checks": expected,
		"row_counts": {name: len(rows) for name, rows in checks.items()},
		"tracking_status": tracking.get("current_status_raw"),
	}
