# Copyright (c) 2026, GoFix and contributors
# "Track My Repair" — public customer-facing page, no login required.
# Accessed via /track-repair?token=<token> or /track-repair?sr=<SR-NAME>&phone=<last4>

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate
import hashlib
import hmac

no_cache = 1
sitemap = 1


def get_context(context):
	"""Render the tracking page based on token or SR+phone verification."""
	context.no_cache = 1
	context.show_sidebar = False
	context.title = "Track My Repair — GoFix"

	token = frappe.form_dict.get("token")
	sr_name = frappe.form_dict.get("sr")
	phone_last4 = frappe.form_dict.get("phone")

	tracking_data = None

	if token:
		tracking_data = _get_by_token(token)
	elif sr_name and phone_last4:
		tracking_data = _get_by_phone(sr_name, phone_last4)

	context.tracking_data = tracking_data
	context.sr_name = sr_name or (tracking_data.get("name") if tracking_data else "")
	context.error = None if tracking_data else "not_found"


def _get_by_token(token):
	"""Look up SR by tracking token (stored in SR or generated via HMAC)."""
	# token = HMAC-SHA256(site_secret, sr_name)[:16]
	# Try all recent SRs (this trades some perf for simplicity; at scale, store tokens)
	sr_list = frappe.get_all(
		"Service Request",
		filters={"status": ["not in", ["Cancelled"]]},
		fields=["name"],
		order_by="creation desc",
		limit=500,
	)
	site_secret = frappe.local.conf.get("secret_key", frappe.local.site)

	for sr in sr_list:
		expected = hmac.new(
			site_secret.encode(), sr.name.encode(), hashlib.sha256
		).hexdigest()[:16]
		if expected == token:
			return _build_tracking_data(sr.name)

	return None


def _get_by_phone(sr_name, phone_last4):
	"""Verify SR exists and phone matches last 4 digits."""
	if not frappe.db.exists("Service Request", sr_name):
		return None

	contact = frappe.db.get_value("Service Request", sr_name, "contact_number") or ""
	clean_phone = "".join(c for c in contact if c.isdigit())

	if clean_phone[-4:] != str(phone_last4).strip()[-4:]:
		return None

	return _build_tracking_data(sr_name)


def _build_tracking_data(sr_name):
	"""Build customer-safe tracking data (no internal details exposed)."""
	sr = frappe.get_doc("Service Request", sr_name)

	# Timeline — customer-friendly status labels
	stage_map = {
		"Draft": {"label": "Request Received", "icon": "inbox", "order": 1},
		"Accepted": {"label": "Device Accepted", "icon": "check-circle", "order": 2},
		"In Service": {"label": "Repair In Progress", "icon": "tool", "order": 3},
		"Completed": {"label": "Repair Completed", "icon": "check", "order": 4},
		"Invoiced": {"label": "Invoice Ready", "icon": "file-text", "order": 5},
		"Delivered": {"label": "Delivered", "icon": "package", "order": 6},
		"Rejected": {"label": "Not Repairable", "icon": "x-circle", "order": -1},
		"Withdrawn": {"label": "Withdrawn", "icon": "arrow-left", "order": -1},
		"Cancelled": {"label": "Cancelled", "icon": "x", "order": -1},
	}

	current = sr.decision or sr.status or "Draft"
	current_info = stage_map.get(current, {"label": current, "order": 0})

	# Build timeline from status_log
	timeline = []
	for row in (sr.get("status_log") or []):
		info = stage_map.get(row.to_status, {"label": row.to_status, "icon": "circle"})
		timeline.append({
			"status": info["label"],
			"datetime": str(row.changed_at) if row.changed_at else "",
			"hours_in_previous": flt(row.time_in_previous_status_hours),
		})

	# Progress steps (for visual progress bar)
	progress_steps = [
		{"label": "Received", "done": current_info["order"] >= 1},
		{"label": "Accepted", "done": current_info["order"] >= 2},
		{"label": "In Repair", "done": current_info["order"] >= 3},
		{"label": "Completed", "done": current_info["order"] >= 4},
		{"label": "Ready", "done": current_info["order"] >= 5},
		{"label": "Delivered", "done": current_info["order"] >= 6},
	]

	# Pending approvals (customer needs to act)
	pending_actions = []
	if sr.get("estimate_approval_pending"):
		latest_v = sr.get("latest_estimate_version") or 0
		for ev in (sr.get("estimate_versions") or []):
			if ev.version_number == latest_v and ev.status in ("Pending", "Sent to Customer"):
				pending_actions.append({
					"type": "estimate_approval",
					"message": f"Please approve the estimate of ₹{flt(ev.estimate_amount):,.0f}",
					"version": latest_v,
				})

	return {
		"name": sr.name,
		"customer_name": sr.customer_name,
		"device": sr.device_item_name or "",
		"serial_no": sr.serial_no or "",
		"brand": sr.brand or "",
		"current_status": current_info["label"],
		"current_status_raw": current,
		"service_date": str(sr.service_date) if sr.service_date else "",
		"expected_completion": str(sr.expected_completion_date) if sr.expected_completion_date else "",
		"estimated_cost": flt(sr.estimated_cost),
		"warranty_status": sr.warranty_status or "",
		"source_store": sr.source_warehouse or "",
		"progress_steps": progress_steps,
		"timeline": timeline,
		"pending_actions": pending_actions,
	}


@frappe.whitelist(allow_guest=True)
def get_tracking_data(sr_name=None, phone_last4=None, token=None) -> dict:
	"""Public API for AJAX tracking lookups."""
	if token:
		data = _get_by_token(token)
	elif sr_name and phone_last4:
		data = _get_by_phone(sr_name, phone_last4)
	else:
		frappe.throw(_("Please provide SR number and phone, or a tracking token"), title=_("Validation Error"))

	if not data:
		frappe.throw(_("Service request not found or phone number does not match"), title=_("Validation Error"))

	return data


def generate_tracking_url(sr_name):
	"""Generate a tokenized URL for sending to customer via WhatsApp."""
	site_secret = frappe.local.conf.get("secret_key", frappe.local.site)
	token = hmac.new(
		site_secret.encode(), sr_name.encode(), hashlib.sha256
	).hexdigest()[:16]

	site_url = frappe.utils.get_url()
	return f"{site_url}/track-repair?token={token}"


@frappe.whitelist(allow_guest=True)
def customer_estimate_action(sr_name, version_number, action, remarks=None, token=None, phone_last4=None) -> dict:
	"""Allow customer to approve/reject an estimate from the tracking page.

	Authentication: must provide either a valid token or SR + phone_last4.
	"""
	# Verify access
	verified = False
	if token:
		data = _get_by_token(token)
		if data and data.get("name") == sr_name:
			verified = True
	elif phone_last4:
		data = _get_by_phone(sr_name, phone_last4)
		if data:
			verified = True

	if not verified:
		frappe.throw(_("Access denied. Invalid token or phone number."), title=_("Validation Error"))

	if action not in ("approve", "reject"):
		frappe.throw(_("Invalid action. Must be 'approve' or 'reject'."), title=_("Validation Error"))

	version_number = int(version_number or 0)

	# Use orchestration engine to process
	from gofix.gofix_services.orchestration import customer_approve_estimate, customer_reject_estimate

	if action == "approve":
		# Run as Administrator since this is a guest endpoint
		frappe.set_user("Administrator")
		try:
			result = customer_approve_estimate(sr_name, version_number, remarks)
		finally:
			frappe.set_user("Guest")
		return {"ok": True, "message": result.get("message", "Approved")}
	else:
		frappe.set_user("Administrator")
		try:
			result = customer_reject_estimate(sr_name, version_number, remarks)
		finally:
			frappe.set_user("Guest")
		return {"ok": True, "message": result.get("message", "Rejected")}
