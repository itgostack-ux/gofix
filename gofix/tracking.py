# Copyright (c) 2026, GoFix and contributors
# Public customer repair tracking helpers.

import hmac
import uuid

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cstr, flt


TRACKING_TOKEN_FIELD = "tracking_token"


def _client_ip() -> str:
	try:
		return frappe.local.request.remote_addr or "unknown"
	except Exception:
		return "unknown"


def _check_lookup_lockout(scope: str) -> None:
	"""Block per IP/scope after repeated failed public lookups."""
	ip = _client_ip()
	key = f"track_repair_fail:{ip}:{scope}"
	fails = int(frappe.cache().get_value(key) or 0)
	if fails >= 5:
		frappe.throw(
			_("Too many failed lookups from your network. Please try again later."),
			frappe.PermissionError,
			title=_("Rate Limit Exceeded"),
		)


def _record_lookup_failure(scope: str) -> None:
	ip = _client_ip()
	key = f"track_repair_fail:{ip}:{scope}"
	fails = int(frappe.cache().get_value(key) or 0)
	frappe.cache().set_value(key, fails + 1, expires_in_sec=3600)


def _clear_lookup_failures(scope: str) -> None:
	ip = _client_ip()
	frappe.cache().delete_value(f"track_repair_fail:{ip}:{scope}")


def _tracking_column_exists() -> bool:
	try:
		return frappe.db.has_column("Service Request", TRACKING_TOKEN_FIELD)
	except Exception:
		return False


def _normalize_token(token: str | None) -> str:
	return cstr(token).strip()


def make_tracking_token(existing_name: str | None = None) -> str:
	"""Create a unique random GUID token for a Service Request."""
	for _ in range(10):
		token = str(uuid.uuid4())
		filters = {TRACKING_TOKEN_FIELD: token}
		if existing_name:
			filters["name"] = ["!=", existing_name]
		if not frappe.db.exists("Service Request", filters):
			return token
	frappe.throw(_("Could not generate a unique tracking token. Please retry."))


def ensure_tracking_token(sr_name: str) -> str:
	"""Return the stored Service Request tracking token, creating it if absent."""
	if not _tracking_column_exists():
		frappe.throw(
			_("Service Request tracking token field is not installed. Please run migration."),
			frappe.ValidationError,
		)

	token = _normalize_token(frappe.db.get_value("Service Request", sr_name, TRACKING_TOKEN_FIELD))
	if token:
		return token

	token = make_tracking_token(existing_name=sr_name)
	frappe.db.set_value(
		"Service Request",
		sr_name,
		TRACKING_TOKEN_FIELD,
		token,
		update_modified=False,
	)
	return token


def _get_by_token(token):
	"""Look up SR by stored random tracking token. No derived-token fallback."""
	_check_lookup_lockout("token")
	token = _normalize_token(token)
	if not token or not _tracking_column_exists():
		_record_lookup_failure("token")
		return None

	rows = frappe.get_all(
		"Service Request",
		filters={
			TRACKING_TOKEN_FIELD: token,
			"status": ["not in", ["Cancelled"]],
		},
		pluck="name",
		limit=1,
	)
	if rows:
		_clear_lookup_failures("token")
		return _build_tracking_data(rows[0])

	_record_lookup_failure("token")
	return None


def _get_by_phone(sr_name, phone_last4):
	"""Verify SR exists and phone last-6 digits match."""
	_check_lookup_lockout(f"phone:{sr_name}")
	if not frappe.db.exists("Service Request", sr_name):
		_record_lookup_failure(f"phone:{sr_name}")
		return None

	contact = frappe.db.get_value("Service Request", sr_name, "contact_number") or ""
	clean_phone = "".join(c for c in contact if c.isdigit())

	provided = "".join(c for c in str(phone_last4 or "") if c.isdigit())
	if len(clean_phone) < 6 or len(provided) < 4:
		_record_lookup_failure(f"phone:{sr_name}")
		return None
	if not hmac.compare_digest(clean_phone[-6:], provided[-6:].rjust(6, "0")):
		_record_lookup_failure(f"phone:{sr_name}")
		return None

	_clear_lookup_failures(f"phone:{sr_name}")
	return _build_tracking_data(sr_name)


def _build_tracking_data(sr_name):
	"""Build customer-safe tracking data."""
	sr = frappe.get_doc("Service Request", sr_name)

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

	timeline = []
	for row in (sr.get("status_log") or []):
		info = stage_map.get(row.to_status, {"label": row.to_status, "icon": "circle"})
		timeline.append({
			"status": info["label"],
			"datetime": str(row.changed_at) if row.changed_at else "",
			"hours_in_previous": flt(row.time_in_previous_status_hours),
		})

	progress_steps = [
		{"label": "Received", "done": current_info["order"] >= 1},
		{"label": "Accepted", "done": current_info["order"] >= 2},
		{"label": "In Repair", "done": current_info["order"] >= 3},
		{"label": "Completed", "done": current_info["order"] >= 4},
		{"label": "Ready", "done": current_info["order"] >= 5},
		{"label": "Delivered", "done": current_info["order"] >= 6},
	]

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
@rate_limit(limit=30, seconds=300, methods=["POST"], ip_based=True)
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
	token = ensure_tracking_token(sr_name)
	site_url = frappe.utils.get_url()
	return f"{site_url}/track-repair?token={token}"


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=10, seconds=300, methods=["POST"], ip_based=True)
def customer_estimate_action(sr_name, version_number, action, remarks=None, token=None, phone_last4=None) -> dict:
	"""Allow customer to approve/reject an estimate from the tracking page.

	Authentication: must provide either a valid stored token or SR + phone_last4.
	"""
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

	from gofix.gofix_services.orchestration import customer_approve_estimate, customer_reject_estimate

	if action == "approve":
		frappe.set_user("Administrator")
		try:
			result = customer_approve_estimate(sr_name, version_number, remarks)
		finally:
			frappe.set_user("Guest")
		return {"ok": True, "message": result.get("message", "Approved")}

	frappe.set_user("Administrator")
	try:
		result = customer_reject_estimate(sr_name, version_number, remarks)
	finally:
		frappe.set_user("Guest")
	return {"ok": True, "message": result.get("message", "Rejected")}
