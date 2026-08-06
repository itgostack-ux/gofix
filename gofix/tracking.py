# Copyright (c) 2026, GoFix and contributors
# Public customer repair tracking helpers.

import hmac
import hashlib
import uuid

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cstr, flt

from gofix.config import get_int_setting

TRACKING_TOKEN_FIELD = "tracking_token"
TRACKING_SALT_FIELD = "tracking_token_salt"


def _atomic_public_counter(key: str, window: int) -> int:
	cache_key = frappe.cache.make_key(key)
	frappe.cache.set(cache_key, 0, nx=True, ex=window)
	value = frappe.cache.incrby(cache_key, 1)
	if frappe.cache.ttl(cache_key) < 0:
		frappe.cache.expire(cache_key, window)
	return int(value)


def _client_ip() -> str:
	try:
		return frappe.local.request.remote_addr or "unknown"
	except Exception:
		return "unknown"


def _check_public_lookup_rate(scope: str) -> None:
	limit = get_int_setting("guest_rate_limit", 30)
	window = get_int_setting("guest_rate_window_seconds", 3600, minimum=60)
	key = f"gofix-public-tracking:{_client_ip()}:{scope}:{window}"
	if _atomic_public_counter(key, window) > limit:
		frappe.throw(
			_("Too many tracking requests from your network. Please try again later."),
			frappe.RateLimitExceededError,
		)


def _check_lookup_lockout(scope: str) -> None:
	"""Block per IP/scope after repeated failed public lookups."""
	ip = _client_ip()
	key = frappe.cache.make_key(f"track_repair_fail:{ip}:{scope}")
	fails = int(frappe.cache.get(key) or 0)
	if fails >= get_int_setting("public_lookup_failure_limit", 5):
		frappe.throw(
			_("Too many failed lookups from your network. Please try again later."),
			frappe.PermissionError,
			title=_("Rate Limit Exceeded"),
		)


def _record_lookup_failure(scope: str) -> None:
	ip = _client_ip()
	key = f"track_repair_fail:{ip}:{scope}"
	_atomic_public_counter(
		key,
		get_int_setting("public_lookup_lockout_seconds", 3600, minimum=60),
	)


def _clear_lookup_failures(scope: str) -> None:
	ip = _client_ip()
	frappe.cache.delete(frappe.cache.make_key(f"track_repair_fail:{ip}:{scope}"))


def _tracking_column_exists() -> bool:
	try:
		return frappe.db.has_column("Service Request", TRACKING_TOKEN_FIELD)
	except Exception:
		return False


def _salt_column_exists() -> bool:
	try:
		return frappe.db.has_column("Service Request", TRACKING_SALT_FIELD)
	except Exception:
		return False


def _normalize_token(token: str | None) -> str:
	return cstr(token).strip()


def tracking_token_digest(token: str | None) -> str:
	token = _normalize_token(token)
	if not token:
		return ""
	return f"sha256:{hashlib.sha256(token.encode()).hexdigest()}"


def _site_tracking_key() -> bytes:
	"""Server-side secret used to derive tracking tokens (never stored per document)."""
	from frappe.utils.password import get_encryption_key

	return cstr(get_encryption_key()).encode()


def make_tracking_salt() -> str:
	"""Mint a random per-document salt for deterministic token derivation."""
	return uuid.uuid4().hex


def derive_tracking_token(sr_name: str, salt: str | None) -> str:
	"""Derive the tracking token as HMAC(site_key, sr_name + salt).

	The plaintext token never rests in the database: only the random salt and
	the sha256 digest of the derived token are stored. The token is therefore
	reproducible server-side (stable customer links) until the salt is rotated.
	"""
	salt = _normalize_token(salt)
	if not sr_name or not salt:
		return ""
	message = f"{cstr(sr_name)}:{salt}".encode()
	return hmac.new(_site_tracking_key(), message, hashlib.sha256).hexdigest()


def rotate_tracking_token(sr_name: str) -> str:
	"""Mint a new salt for a Service Request, revoking all previously issued links."""
	for _ in range(10):
		salt = make_tracking_salt()
		token = derive_tracking_token(sr_name, salt)
		digest = tracking_token_digest(token)
		if frappe.db.exists(
			"Service Request",
			{TRACKING_TOKEN_FIELD: digest, "name": ["!=", sr_name]},
		):
			continue
		frappe.db.set_value(
			"Service Request",
			sr_name,
			{TRACKING_TOKEN_FIELD: digest, TRACKING_SALT_FIELD: salt},
			update_modified=False,
		)
		return token
	frappe.throw(_("Could not generate a unique tracking token. Please retry."))


def ensure_tracking_token(sr_name: str) -> str:
	"""Return the Service Request's stable tracking token, minting it only once.

	Repeat calls (one per outbound notification) reproduce the same token, so
	earlier customer links keep working. Legacy rows minted before salted
	derivation carry only a random-token digest; those are rotated once here
	and are stable from then on. Explicit revocation = rotate_tracking_token.
	"""
	if not _tracking_column_exists() or not _salt_column_exists():
		frappe.throw(
			_("Service Request tracking token fields are not installed. Please run migration."),
			frappe.ValidationError,
		)

	row = frappe.db.get_value(
		"Service Request",
		sr_name,
		[TRACKING_TOKEN_FIELD, TRACKING_SALT_FIELD],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Service Request {0} not found").format(sr_name))

	salt = _normalize_token(row.get(TRACKING_SALT_FIELD))
	if salt:
		token = derive_tracking_token(sr_name, salt)
		digest = tracking_token_digest(token)
		if cstr(row.get(TRACKING_TOKEN_FIELD)) != digest:
			# Heal digest drift (e.g. rows restored without a matching digest).
			frappe.db.set_value(
				"Service Request",
				sr_name,
				TRACKING_TOKEN_FIELD,
				digest,
				update_modified=False,
			)
		return token

	# Legacy Service Request without a salt: rotate once to the deterministic
	# scheme. The previous random link is invalidated exactly as it was under
	# the old mint-per-call behaviour; every later call is stable.
	return rotate_tracking_token(sr_name)


def _get_by_token(token):
	"""Look up SR by stored random tracking token. No derived-token fallback."""
	_check_public_lookup_rate("token")
	_check_lookup_lockout("token")
	token = _normalize_token(token)
	if not token or not _tracking_column_exists():
		_record_lookup_failure("token")
		return None

	digest = tracking_token_digest(token)
	rows = frappe.get_all(
		"Service Request",
		filters={
			TRACKING_TOKEN_FIELD: digest,
			"decision": ["not in", ["Cancelled"]],
		},
		pluck="name",
		limit=1,
	)
	if not rows:
		rows = frappe.get_all(
			"Service Request",
			filters={
				TRACKING_TOKEN_FIELD: token,
				"decision": ["not in", ["Cancelled"]],
			},
			pluck="name",
			limit=1,
		)
		if rows:
			frappe.db.set_value(
				"Service Request",
				rows[0],
				TRACKING_TOKEN_FIELD,
				digest,
				update_modified=False,
			)
	if rows:
		_clear_lookup_failures("token")
		return _build_tracking_data(rows[0])

	_record_lookup_failure("token")
	return None


def _get_by_phone(sr_name, phone_last4):
	"""Verify SR exists and the provided phone suffix is exactly four digits."""
	_check_public_lookup_rate("phone")
	_check_lookup_lockout(f"phone:{sr_name}")
	if not frappe.db.exists("Service Request", sr_name):
		_record_lookup_failure(f"phone:{sr_name}")
		return None

	contact = frappe.db.get_value("Service Request", sr_name, "contact_number") or ""
	clean_phone = "".join(c for c in contact if c.isdigit())

	provided = "".join(c for c in str(phone_last4 or "") if c.isdigit())
	if len(clean_phone) < 4 or len(provided) != 4:
		_record_lookup_failure(f"phone:{sr_name}")
		return None
	if not hmac.compare_digest(clean_phone[-4:], provided):
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

	current = sr.decision or "Draft"
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


@frappe.whitelist(allow_guest=True, methods=["POST"])
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


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=300, methods=["POST"], ip_based=True)
def customer_estimate_action(sr_name, version_number, action, remarks=None, token=None) -> dict:
	"""Allow customer to approve/reject an estimate from the tracking page.

	Authentication requires the Service Request's stored high-entropy tracking token.
	"""
	data = _get_by_token(token) if token else None
	if not data or data.get("name") != sr_name:
		frappe.throw(_("Access denied. A valid customer tracking token is required."), frappe.PermissionError)

	if action not in ("approve", "reject"):
		frappe.throw(_("Invalid action. Must be 'approve' or 'reject'."), title=_("Validation Error"))

	version_number = int(version_number or 0)

	from gofix.gofix_services.orchestration import _apply_customer_estimate_action

	result = _apply_customer_estimate_action(
		sr_name,
		version_number,
		action,
		remarks,
		authorization="customer_token",
	)
	return {
		"ok": True,
		"message": result.get("message", "Approved" if action == "approve" else "Rejected"),
	}
