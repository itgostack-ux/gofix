# Copyright (c) 2025, GoStack and contributors
# Delivery control, estimate approval, and decision approval APIs for GoFix Service Orders

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, flt, getdate, get_datetime, now, now_datetime, nowdate, random_string, today
import json
import secrets

from ch_item_master.ch_core.cost_center import apply_cost_center
from gofix.config import get_int_setting, require_role_setting
from gofix.security import assert_service_request_access


def _get_scoped_service_order(service_order, permission_type="read"):
	so = frappe.get_doc("Sales Order", service_order)
	if not so.is_service_order or not so.service_request:
		frappe.throw(_("Not a linked Service Order"), frappe.PermissionError, title=_("API Error"))
	so.check_permission(permission_type)
	assert_service_request_access(so.service_request, permission_type=permission_type)
	return so


def _require_sales_operation_role() -> None:
	frappe.has_permission("Service Request", ptype="write", throw=True)


def _require_billing_role() -> None:
	frappe.has_permission("Sales Invoice", ptype="create", throw=True)


def _require_store_operation_role() -> None:
	frappe.has_permission("Service Request", ptype="write", throw=True)


def _require_service_manager_role() -> None:
	frappe.has_permission("Service Request", ptype="submit", throw=True)


def _require_reference_read(action, doctypes, role_field="service_access_roles") -> None:
	for doctype in doctypes:
		frappe.has_permission(doctype, "read", throw=True)


def _bounded_name_list(value, label) -> list[str]:
	if isinstance(value, str):
		value = json.loads(value)
	if not isinstance(value, list):
		frappe.throw(_("{0} must be a JSON list.").format(label), frappe.ValidationError)
	limit = get_int_setting("token_queue_limit", 200)
	names = list(
		dict.fromkeys(
			str(name).strip()
			for name in value
			if name is not None and str(name).strip()
		)
	)
	if len(names) > limit:
		frappe.throw(
			_("A maximum of {0} {1} can be processed at once.").format(limit, label.lower()),
			frappe.ValidationError,
		)
	return names


def _ensure_future_datetime(value, label):
	"""Validate that a provided datetime exists and is not in the past."""
	if not value:
		frappe.throw(_("{0} is required").format(label), title=_("Missing Date"))
	dt = get_datetime(value)
	if dt < get_datetime():
		frappe.throw(_("{0} cannot be in the past.").format(label), title=_("Invalid Date"))
	return dt


def _validate_logistics_address(address):
	"""Require a complete pickup address including a usable pincode."""
	address = str(address or "").strip()
	if not address:
		frappe.throw(_("Pickup address is required."), title=_("Missing Address"))
	if len(address) < 15:
		frappe.throw(
			_("Pickup address is too short. Please include landmark and pincode."),
			title=_("Incomplete Address"),
		)
	digits = "".join(ch for ch in address if ch.isdigit())
	if len(digits) < 6:
		frappe.throw(
			_("Pickup address should include a valid 6-digit pincode."),
			title=_("Pincode Required"),
		)
	return address


def _audit_service_update(sr, event_type, before=None, after=None, remarks=None):
	"""Best-effort business audit entry for service logistics milestones."""
	try:
		from ch_pos.audit import log_business_event

		log_business_event(
			event_type=event_type,
			ref_doctype="Service Request",
			ref_name=sr.name,
			before=before,
			after=after,
			remarks=remarks,
			store=sr.get("service_center") or sr.get("warehouse") or sr.get("store"),
			company=sr.get("company"),
			user=frappe.session.user,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Service audit log failed for {sr.name}")


def _otp_scope_lock(scope):
	return frappe.cache().lock(
		f"gofix-otp-lock::{scope}",
		timeout=10,
		blocking_timeout=5,
	)


def _atomic_counter(key, window):
	cache = frappe.cache
	cache_key = cache.make_key(key)
	cache.set(cache_key, 0, nx=True, ex=window)
	value = cache.incrby(cache_key, 1)
	if cache.ttl(cache_key) < 0:
		cache.expire(cache_key, window)
	return cint(value)


def _reset_atomic_counter(key, window):
	frappe.cache.setex(frappe.cache.make_key(key), window, 0)


def _enforce_otp_rate_limit(action, scope):
	if action == "request":
		limit = get_int_setting("otp_request_limit", 5)
		window = get_int_setting("otp_request_window_seconds", 3600, minimum=60)
	else:
		limit = get_int_setting("otp_verify_limit", 10)
		window = get_int_setting("otp_verify_window_seconds", 300, minimum=60)
	key = f"gofix-otp-rate::{action}::{frappe.session.user}::{scope}"
	count = _atomic_counter(key, window)
	if count > limit:
		frappe.throw(_("Too many OTP attempts. Please try again later."), frappe.RateLimitExceededError)


def _otp_attempt_limits():
	return (
		get_int_setting("otp_max_attempts", 5),
		get_int_setting("otp_lockout_seconds", 900, minimum=60),
	)


# ── Delivery Control ─────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def generate_delivery_otp(service_order) -> dict:
	"""Generate and send OTP for device handover verification."""
	_require_sales_operation_role()
	so = _get_scoped_service_order(service_order, "write")
	_enforce_otp_rate_limit("request", f"delivery::{so.name}")
	frappe.db.sql("SELECT name FROM `tabSales Order` WHERE name = %s FOR UPDATE", (so.name,))
	so.reload()
	now_value = now_datetime()
	if so.get("delivery_otp_locked_until") and get_datetime(so.delivery_otp_locked_until) > now_value:
		frappe.throw(_("Delivery OTP is temporarily locked after repeated failures."), frappe.PermissionError)

	otp = str(secrets.randbelow(900000) + 100000)
	so.db_set({
		"delivery_otp": frappe.utils.password.encrypt(otp),
		"delivery_otp_verified": 0,
		"delivery_otp_sent_at": now_value,
		"delivery_otp_attempts": 0,
		"delivery_otp_locked_until": None,
		"delivery_otp_consumed_at": None,
	}, update_modified=False)

	# Send OTP to customer
	_send_delivery_otp(so, otp)

	return {"message": _("OTP sent to customer"), "otp_sent": True}


@frappe.whitelist(methods=["POST"])
def verify_delivery_otp(service_order, otp_input) -> dict:
	"""Verify the delivery OTP entered by customer."""
	_require_sales_operation_role()
	so = _get_scoped_service_order(service_order, "write")
	_enforce_otp_rate_limit("verify", f"delivery::{so.name}")
	frappe.db.sql("SELECT name FROM `tabSales Order` WHERE name = %s FOR UPDATE", (so.name,))
	so.reload()
	if so.get("delivery_otp_verified"):
		return {"message": _("OTP was already verified."), "verified": True, "already_verified": True}
	now_value = now_datetime()
	if so.get("delivery_otp_locked_until") and get_datetime(so.delivery_otp_locked_until) > now_value:
		return {"message": _("Delivery OTP is temporarily locked."), "verified": False, "locked": True}
	stored_otp = so.delivery_otp
	if not stored_otp or not so.get("delivery_otp_sent_at"):
		return {"message": _("No active delivery OTP. Generate a new OTP."), "verified": False}
	ttl = get_int_setting("delivery_otp_ttl_seconds", 600, minimum=60)
	if get_datetime(so.delivery_otp_sent_at) < add_to_date(now_value, seconds=-ttl):
		so.db_set({"delivery_otp": None, "delivery_otp_attempts": 0}, update_modified=False)
		return {"message": _("Delivery OTP has expired."), "verified": False, "expired": True}

	from ch_item_master.ch_core.shadow_live import master_otp_matches

	try:
		decrypted = frappe.utils.password.decrypt(stored_otp)
	except Exception:
		decrypted = stored_otp

	valid = master_otp_matches(otp_input) or secrets.compare_digest(
		str(otp_input or "").strip(), str(decrypted or "").strip()
	)
	if not valid:
		max_attempts, lockout_seconds = _otp_attempt_limits()
		attempts = cint(so.get("delivery_otp_attempts")) + 1
		updates = {"delivery_otp_attempts": attempts}
		locked = attempts >= max_attempts
		if locked:
			updates.update({
				"delivery_otp": None,
				"delivery_otp_locked_until": add_to_date(now_value, seconds=lockout_seconds),
			})
		so.db_set(updates, update_modified=False)
		return {
			"message": _("Invalid OTP." if not locked else "Maximum OTP attempts exceeded."),
			"verified": False,
			"locked": locked,
			"attempts_remaining": max(max_attempts - attempts, 0),
		}

	so.db_set({
		"delivery_otp": None,
		"delivery_otp_verified": 1,
		"delivery_otp_attempts": 0,
		"delivery_otp_locked_until": None,
		"delivery_otp_consumed_at": now_value,
	}, update_modified=False)
	return {
		"message": _("Shadow-live master OTP accepted." if master_otp_matches(otp_input) else "OTP verified successfully"),
		"verified": True,
	}


@frappe.whitelist()
def validate_delivery_readiness(service_order) -> dict:
	"""Check all delivery gates before allowing device handover."""
	so = _get_scoped_service_order(service_order, "read")

	blockers = []

	# Gate 1: QC must be passed
	if getattr(so, "qc_status", None) != "Pass":
		blockers.append(_("QC not passed (current: {0})").format(so.qc_status or "Pending"))

	# Gate 2: Payment verified
	# sales_order is a Sales Invoice Item field, not an invoice header field.
	has_unpaid = frappe.get_all(
		"Sales Invoice",
		filters=[
			["Sales Invoice Item", "sales_order", "=", service_order],
			["outstanding_amount", ">", 0],
			["docstatus", "=", 1],
		],
		pluck="name",
		limit=1,
	)
	if has_unpaid:
		blockers.append(_("Outstanding payment exists"))

	# Gate 3: OTP verified
	if not getattr(so, "delivery_otp_verified", None):
		blockers.append(_("Delivery OTP not verified"))

	# Gate 4: Accessories returned
	if getattr(so, "accessories_received", None) and not getattr(so, "accessories_returned", None):
		blockers.append(_("Accessories not confirmed as returned"))

	return {
		"ready": len(blockers) == 0,
		"blockers": blockers,
	}


@frappe.whitelist(methods=["POST"])
def complete_delivery(service_order, remarks=None) -> dict:
	"""Mark device as delivered after all gates pass."""
	_require_sales_operation_role()
	so = _get_scoped_service_order(service_order, "write")
	frappe.db.sql("SELECT name FROM `tabSales Order` WHERE name = %s FOR UPDATE", (so.name,))
	so.reload()

	readiness = validate_delivery_readiness(service_order)
	if not readiness["ready"]:
		frappe.throw(
			_("Cannot deliver. Blockers: {0}").format(", ".join(readiness["blockers"])),
			title=_("Delivery Blocked"),
		)

	so.db_set("delivered_datetime", now(), update_modified=False)
	so.db_set("actual_delivery_date", today(), update_modified=False)
	so.db_set("delivery_ready_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("delivery_remarks", remarks, update_modified=False)

	# Update SR status
	if so.service_request:
		frappe.get_doc("Service Request", so.service_request).db_set({
			"decision": "Delivered",
		}, update_modified=True)

	frappe.msgprint(_("Device delivered successfully"), indicator="green")
	return {"message": "Delivered"}


def _send_delivery_otp(so, otp):
	"""Send delivery OTP via SMS and/or email."""
	from ch_item_master.ch_core.shadow_live import suppress_customer_comms

	if suppress_customer_comms():
		frappe.logger("shadow_live").info(f"Delivery OTP suppressed (shadow live) for {so.name}")
		return

	sr = None
	if so.service_request:
		sr = frappe.get_doc("Service Request", so.service_request)

	customer_mobile = sr.contact_number if sr else None
	customer_email = sr.email if sr else None
	customer_name = sr.customer_name if sr else so.customer_name

	if customer_mobile:
		try:
			from frappe.core.doctype.sms_settings.sms_settings import send_sms
			sms_text = (
				f"GoFix: Your device collection OTP is {otp}. "
				f"Share this with the store to collect your device. "
				f"Service Order: {so.name}"
			)
			send_sms([customer_mobile], sms_text)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Delivery OTP SMS failed")

	if customer_email:
		try:
			so_url = frappe.utils.get_url_to_form("Sales Order", so.name)
			frappe.sendmail(
				recipients=[customer_email],
				subject=f"GoFix Services | Device Collection OTP | {so.name}",
				message=(
					"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
					"<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
					"<div style='padding:16px'>"
					f"<p>Dear {frappe.utils.escape_html(customer_name or 'Customer')},</p>"
					f"<p>Your OTP for device collection is: <span style='font-size:22px;font-weight:700;letter-spacing:4px'>{otp}</span></p>"
					f"<p>Please share this OTP with the store executive at handover.</p>"
					f"<p><b>Service Order:</b> {so.name}</p>"
					f"<p><a href='{so_url}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Order</a></p>"
					"</div></div>"
				),
				reference_doctype="Sales Order",
				reference_name=so.name,
				now=True,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Delivery OTP email failed")


# ── Billing location guard + remote-billing customer OTP ─────────────
#
# Invoicing must happen at the store where the customer handed in the
# device (source_warehouse), after the return transfer is recorded. Billing
# anywhere else requires the customer's explicit OTP consent, sent via
# WhatsApp / SMS / email.

def billing_location_status(sr) -> dict:
	"""Where is the device vs where it must be billed."""
	if isinstance(sr, str):
		sr = frappe.get_doc("Service Request", sr)
	home = sr.source_warehouse
	transfer = (sr.get("transfer_status") or "").strip()
	if transfer in ("", "Not Transferred", "Returned to Store"):
		device_at = sr.get("current_location") or home
	elif transfer in ("In Transit", "Return In Transit"):
		device_at = None  # on the road
	else:
		device_at = sr.get("current_location") or sr.get("transferred_to_store")
	at_home = bool(home and device_at == home)
	verified = bool(frappe.cache().get_value(f"gofix_remote_billing_ok::{sr.name}"))
	return {
		"home_store": home,
		"device_at": device_at,
		"transfer_status": transfer or "Not Transferred",
		"at_home_store": at_home,
		"requires_remote_otp": not at_home,
		"otp_verified": verified,
	}


def assert_billing_location(sr, remote_otp=None) -> None:
	"""Block off-store invoicing unless customer OTP consent is verified."""
	if isinstance(sr, str):
		sr = frappe.get_doc("Service Request", sr)
	status = billing_location_status(sr)
	if status["at_home_store"] or status["otp_verified"]:
		return
	if remote_otp:
		verification = verify_remote_billing_otp(sr.name, remote_otp)
		if verification.get("verified"):
			return
	frappe.throw(
		_(
			"Invoicing is allowed only at the device's home store {0} — the device is "
			"currently at {1} ({2}). Either record the return transfer to the store, or "
			"take the customer's consent: send them a billing OTP (WhatsApp / email) and "
			"enter it here."
		).format(
			frappe.bold(status["home_store"] or "—"),
			frappe.bold(status["device_at"] or _("in transit")),
			status["transfer_status"],
		),
		title=_("Bill at Home Store"),
	)


@frappe.whitelist(methods=["POST"])
def request_remote_billing_otp(service_request) -> dict:
	"""Send the customer a billing-consent OTP via WhatsApp / SMS / email."""
	_require_billing_role()
	sr = assert_service_request_access(service_request, permission_type="write")
	status = billing_location_status(sr)
	if status["at_home_store"]:
		return {"otp_sent": False, "message": _("Device is at its home store — no OTP needed.")}
	_enforce_otp_rate_limit("request", f"remote-billing::{sr.name}")

	otp = str(secrets.randbelow(900000) + 100000)
	otp_ttl = get_int_setting("remote_billing_otp_ttl_seconds", 600, minimum=60)
	otp_valid_minutes = max(1, (otp_ttl + 59) // 60)
	otp_key = f"gofix_remote_billing_otp::{sr.name}"
	attempts_key = f"gofix_remote_billing_attempts::{sr.name}"
	lockout_key = f"gofix_remote_billing_lockout::{sr.name}"
	with _otp_scope_lock(f"remote-billing::{sr.name}"):
		if frappe.cache().get_value(lockout_key):
			frappe.throw(_("Remote billing OTP is temporarily locked."), frappe.PermissionError)
		frappe.cache().set_value(otp_key, frappe.utils.password.encrypt(otp), expires_in_sec=otp_ttl)
		_reset_atomic_counter(attempts_key, otp_ttl)

	from ch_item_master.ch_core.shadow_live import suppress_customer_comms

	if suppress_customer_comms():
		# Shadow-live pilot: nothing goes to the customer — staff use the
		# configured master OTP instead.
		return {
			"otp_sent": True,
			"channels": ["Shadow Live"],
			"message": _("Shadow live — no OTP sent to the customer. Enter the master OTP."),
		}

	channels = []
	phone = (sr.contact_number or "").strip()
	email = (sr.get("email") or "").strip()

	if phone:
		try:
			from ch_item_master.ch_core.whatsapp import send_template_message

			send_template_message(
				phone=phone,
				event="general_otp",
				body_values={"1": otp},
				customer_name=sr.customer_name,
				ref_doctype="Service Request",
				ref_name=sr.name,
				company=sr.company,
			)
			channels.append("WhatsApp")
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Remote billing OTP WhatsApp failed")
		try:
			from frappe.core.doctype.sms_settings.sms_settings import send_sms

			send_sms(
				[phone],
				f"GoFix: OTP {otp} to approve billing of your repair {sr.name} at a different "
				f"store. Valid {otp_valid_minutes} minutes. Do not share unless you authorise this bill.",
			)
			channels.append("SMS")
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Remote billing OTP SMS failed")

	if email:
		try:
			frappe.sendmail(
				recipients=[email],
				subject=f"GoFix Services | Billing Approval OTP | {sr.name}",
				message=(
					"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
					"<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
					"<div style='padding:16px'>"
					f"<p>Dear {frappe.utils.escape_html(sr.customer_name or 'Customer')},</p>"
					f"<p>Your repair <b>{sr.name}</b> is being billed at a location other than the "
					f"store where you handed in your device. If you approve, share this OTP with "
					f"the executive: <span style='font-size:22px;font-weight:700;letter-spacing:4px'>{otp}</span></p>"
					f"<p>The OTP is valid for {otp_valid_minutes} minutes. If you did not request this, ignore this email.</p>"
					"</div></div>"
				),
				reference_doctype="Service Request",
				reference_name=sr.name,
				now=True,
			)
			channels.append("Email")
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Remote billing OTP email failed")

	if not channels:
		with _otp_scope_lock(f"remote-billing::{sr.name}"):
			frappe.cache().delete_value(otp_key)
			frappe.cache().delete_value(attempts_key)
		frappe.throw(
			_("Could not send OTP — no reachable customer phone or email on {0}.").format(sr.name),
			title=_("OTP Not Sent"),
		)
	return {"otp_sent": True, "channels": channels, "message": _("OTP sent via {0}").format(", ".join(channels))}


@frappe.whitelist(methods=["POST"])
def verify_remote_billing_otp(service_request, otp) -> dict:
	"""Verify the customer's billing-consent OTP."""
	_require_billing_role()
	sr = assert_service_request_access(service_request, permission_type="write")
	_enforce_otp_rate_limit("verify", f"remote-billing::{sr.name}")
	consent_ttl = get_int_setting("remote_billing_consent_ttl_seconds", 1800, minimum=60)
	consent_minutes = max(1, (consent_ttl + 59) // 60)
	from ch_item_master.ch_core.shadow_live import master_otp_matches
	otp_key = f"gofix_remote_billing_otp::{service_request}"
	attempts_key = f"gofix_remote_billing_attempts::{service_request}"
	lockout_key = f"gofix_remote_billing_lockout::{service_request}"
	consent_key = f"gofix_remote_billing_ok::{service_request}"
	with _otp_scope_lock(f"remote-billing::{service_request}"):
		if frappe.cache().get_value(lockout_key):
			return {"verified": False, "locked": True, "message": _("Remote billing OTP is temporarily locked.")}
		stored = frappe.cache().get_value(otp_key)
		if not stored:
			return {"verified": False, "expired": True, "message": _("No active OTP. Send a new one.")}
		try:
			decrypted = frappe.utils.password.decrypt(stored)
		except Exception:
			decrypted = stored
		shadow_match = master_otp_matches(otp)
		valid = shadow_match or secrets.compare_digest(
			str(otp or "").strip(), str(decrypted or "").strip()
		)
		if not valid:
			max_attempts, lockout_seconds = _otp_attempt_limits()
			attempts = _atomic_counter(
				attempts_key,
				get_int_setting("remote_billing_otp_ttl_seconds", 600, minimum=60),
			)
			locked = attempts >= max_attempts
			if locked:
				frappe.cache().delete_value(otp_key)
				frappe.cache().delete_value(attempts_key)
				frappe.cache().set_value(lockout_key, 1, expires_in_sec=lockout_seconds)
			return {
				"verified": False,
				"locked": locked,
				"attempts_remaining": max(max_attempts - attempts, 0),
				"message": _("Invalid OTP." if not locked else "Maximum OTP attempts exceeded."),
			}

		audit_message = (
			_("Off-store billing unlocked via shadow-live master OTP (by {0}).")
			if shadow_match
			else _("Customer approved off-store billing via OTP (verified by {0}).")
		).format(frappe.session.user)
		sr.add_comment("Comment", audit_message)
		frappe.cache().delete_value(otp_key)
		frappe.cache().delete_value(attempts_key)
		frappe.cache().delete_value(lockout_key)
		frappe.cache().set_value(consent_key, 1, expires_in_sec=consent_ttl)
	return {
		"verified": True,
		"message": (
			_("Shadow-live master OTP accepted — billing unlocked.")
			if shadow_match
			else _("Customer consent verified — billing unlocked for {0} minutes.").format(consent_minutes)
		),
	}


# ── Estimate Approval Flow ───────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def send_estimate_to_customer(service_order, send_via="Email") -> dict:
	"""Send repair estimate to customer for approval."""
	_require_sales_operation_role()
	so = _get_scoped_service_order(service_order, "write")

	so.db_set("estimate_sent", 1, update_modified=False)
	so.db_set("estimate_sent_datetime", now(), update_modified=False)
	so.db_set("estimate_sent_via", send_via, update_modified=False)
	so.db_set("estimate_approval_status", "Pending", update_modified=False)

	so.db_set(
		"estimate_expiry_date",
		add_days(today(), get_int_setting("estimate_expiry_days", 3)),
		update_modified=False,
	)

	_send_estimate_notification(so, send_via)

	return {"message": _("Estimate sent to customer via {0}").format(send_via)}


@frappe.whitelist(methods=["POST"])
def customer_approve_estimate(service_order, remarks=None) -> dict:
	"""Record an audited staff override of the customer estimate decision."""
	require_role_setting('estimate_decision_override_roles', action=_('override a customer estimate decision'))
	if not (remarks or "").strip():
		frappe.throw(_("Override remarks are required."), frappe.ValidationError)
	so = _get_scoped_service_order(service_order, "write")

	if getattr(so, "estimate_approval_status", None) not in ("Pending", None, ""):
		frappe.throw(_("Estimate is not pending approval (current: {0})").format(
			so.estimate_approval_status))

	# Check expiry
	if so.estimate_expiry_date and getdate(so.estimate_expiry_date) < getdate(today()):
		so.db_set("estimate_approval_status", "Expired", update_modified=False)
		frappe.throw(_("This estimate has expired. Please request a new estimate."), title=_("API Error"))

	so.db_set("estimate_approval_status", "Customer Approved", update_modified=False)
	so.db_set("estimate_approved_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("estimate_customer_remarks", remarks, update_modified=False)
	so.add_comment(
		"Comment",
		_("Customer estimate approval overridden by {0}: {1}").format(frappe.session.user, remarks),
	)

	frappe.msgprint(_("Estimate approved by customer"), indicator="green")
	return {"message": "Estimate approved"}


@frappe.whitelist(methods=["POST"])
def customer_reject_estimate(service_order, remarks=None) -> dict:
	"""Record an audited staff override of the customer estimate decision."""
	require_role_setting('estimate_decision_override_roles', action=_('override a customer estimate decision'))
	if not (remarks or "").strip():
		frappe.throw(_("Override remarks are required."), frappe.ValidationError)
	so = _get_scoped_service_order(service_order, "write")

	so.db_set("estimate_approval_status", "Customer Rejected", update_modified=False)
	so.db_set("estimate_approved_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("estimate_customer_remarks", remarks, update_modified=False)
	so.add_comment(
		"Comment",
		_("Customer estimate rejection overridden by {0}: {1}").format(frappe.session.user, remarks),
	)

	frappe.msgprint(_("Estimate rejected by customer"), indicator="orange")
	return {"message": "Estimate rejected"}


def _send_estimate_notification(so, send_via):
	"""Send estimate notification to customer."""
	sr = None
	if so.service_request:
		sr = frappe.get_doc("Service Request", so.service_request)

	customer_email = sr.email if sr else None
	customer_mobile = sr.contact_number if sr else None
	customer_name = sr.customer_name if sr else so.customer_name
	total = flt(so.grand_total) or flt(so.total)

	if send_via in ("Email", "WhatsApp") and customer_email:
		try:
			so_url = frappe.utils.get_url_to_form("Sales Order", so.name)
			frappe.sendmail(
				recipients=[customer_email],
				subject=f"GoFix Services | Repair Estimate | {so.name}",
				message=(
					"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:680px;border:1px solid #e5e7eb;border-radius:10px;overflow:hidden'>"
					"<div style='background:#0f172a;color:#ffffff;padding:12px 16px;font-weight:600'>GoFix Services</div>"
					"<div style='padding:16px'>"
					f"<p>Dear {frappe.utils.escape_html(customer_name or 'Customer')},</p>"
					"<p>Your repair estimate is ready:</p>"
					f"<p><b>Device:</b> {frappe.utils.escape_html(getattr(so, 'device_model', '') or 'N/A')}<br>"
					f"<b>Issue:</b> {frappe.utils.escape_html(getattr(so, 'issue_description', '') or 'N/A')}<br>"
					f"<b>Estimated Cost:</b> ₹{total:,.2f}<br>"
					f"<b>Valid Until:</b> {so.estimate_expiry_date or 'N/A'}</p>"
					f"<p><a href='{so_url}' style='background:#0b57d0;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:6px;display:inline-block;font-weight:600'>Open Service Order</a></p>"
					"</div></div>"
				),
				reference_doctype="Sales Order",
				reference_name=so.name,
				now=True,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Estimate notification email failed")

	if send_via in ("SMS", "WhatsApp") and customer_mobile:
		try:
			from frappe.core.doctype.sms_settings.sms_settings import send_sms
			sms_text = (
				f"GoFix: Repair estimate for {so.name} is ₹{total:,.0f}. "
				f"Valid until {so.estimate_expiry_date or 'N/A'}. "
				f"Please contact us to approve/reject."
			)
			send_sms([customer_mobile], sms_text)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Estimate notification SMS failed for {so.name}")


def expire_pending_estimates():
	"""Expire one bounded batch of service estimates past their expiry date."""
	batch_limit = min(get_int_setting("scheduler_batch_limit", 500, minimum=1), 5000)
	filters = {
		"estimate_approval_status": "Pending",
		"estimate_expiry_date": ("<", today()),
		"is_service_order": 1,
	}
	rows = frappe.get_all(
		"Sales Order",
		filters=filters,
		pluck="name",
		order_by="estimate_expiry_date asc, name asc",
		limit=batch_limit + 1,
	)
	expired = rows[:batch_limit]
	if expired:
		frappe.db.sql(
			"""
				UPDATE `tabSales Order`
				SET `estimate_approval_status` = 'Expired'
				WHERE `name` IN %(names)s
				  AND `estimate_approval_status` = 'Pending'
				  AND `estimate_expiry_date` < %(today)s
				  AND `is_service_order` = 1
			""",
			{"names": tuple(expired), "today": today()},
		)
		frappe.logger("gofix").info(f"Expired {len(expired)} pending estimates")
	return {"expired": len(expired), "has_more": len(rows) > batch_limit}


# ── Decision Approval (maker-checker) ────────────────────────────────

@frappe.whitelist(methods=["POST"])
def approve_decision(service_order, remarks=None) -> dict:
	"""Manager approves a repair decision that requires approval."""
	_require_store_operation_role()
	so = _get_scoped_service_order(service_order, "write")

	so.db_set("decision_approval_status", "Approved", update_modified=False)
	so.db_set("decision_approved_by", frappe.session.user, update_modified=False)
	so.db_set("decision_approval_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("decision_approval_remarks", remarks, update_modified=False)

	frappe.msgprint(_("Decision approved"), indicator="green")
	return {"message": "Decision approved"}


@frappe.whitelist(methods=["POST"])
def reject_decision(service_order, remarks=None) -> dict:
	"""Manager rejects a repair decision."""
	_require_store_operation_role()
	so = _get_scoped_service_order(service_order, "write")

	so.db_set("decision_approval_status", "Rejected", update_modified=False)
	so.db_set("decision_approved_by", frappe.session.user, update_modified=False)
	so.db_set("decision_approval_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("decision_approval_remarks", remarks, update_modified=False)

	frappe.msgprint(_("Decision rejected"), indicator="orange")
	return {"message": "Decision rejected"}


# ── Advance Refund Flow ──────────────────────────────────────────────


def _advance_refund_result(payment_entry, amount, already_exists=False) -> dict:
	return {
		"payment_entry": payment_entry.name,
		"amount": flt(amount),
		"docstatus": cint(payment_entry.docstatus),
		"workflow_state": payment_entry.get("workflow_state") or "",
		"already_exists": already_exists,
	}


def _route_advance_refund_for_approval(payment_entry):
	from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name

	workflow_name = get_workflow_name("Payment Entry")
	if not workflow_name:
		frappe.has_permission("Payment Entry", "submit", throw=True)
		payment_entry.submit()
		return payment_entry

	workflow = frappe.get_cached_doc("Workflow", workflow_name)
	state_docstatus = {row.state: cint(row.doc_status) for row in workflow.states}
	transitions = get_transitions(payment_entry, workflow=workflow)
	approval_routes = [
		transition
		for transition in transitions
		if state_docstatus.get(transition.next_state) == 0
		and transition.next_state != payment_entry.get(workflow.workflow_state_field)
	]
	if len(approval_routes) != 1:
		frappe.throw(
			_("The active Payment Entry workflow does not provide one permitted approval-routing action for your user."),
			frappe.PermissionError,
		)
	return apply_workflow(payment_entry, approval_routes[0].action)


@frappe.whitelist(methods=["POST"])
def process_advance_refund(service_request, amount=None, reason=None) -> dict:
	"""Refund advance payment when device is not repairable.

	Creates and submits a Payment Entry (refund) and updates the Service Request.
	If *amount* is omitted, refunds the full advance_amount.
	"""
	_require_service_manager_role()
	sr = assert_service_request_access(service_request, permission_type="write")
	locked_name = frappe.db.get_value("Service Request", sr.name, "name", for_update=True)
	if not locked_name:
		frappe.throw(_("Service Request {0} no longer exists.").format(sr.name), frappe.DoesNotExistError)
	sr.reload()
	assert_service_request_access(sr, permission_type="write")

	advance = flt(sr.advance_amount)
	if advance <= 0:
		frappe.throw(_("No advance payment recorded on this Service Request"), title=_("API Error"))

	explicit_amount = amount not in (None, "")
	refund_amount = flt(amount) if explicit_amount else advance
	if refund_amount <= 0:
		frappe.throw(_("Refund amount must be greater than zero."), frappe.ValidationError)
	if refund_amount > advance:
		frappe.throw(_("Refund amount (₹{0}) cannot exceed advance (₹{1})").format(
			refund_amount, advance))

	company = sr.company
	if not company:
		frappe.throw(_("The Service Request must have a company before an advance can be refunded."), frappe.ValidationError)

	reference_no = f"Refund-{sr.name}"
	existing_name = (sr.get("advance_refund_entry") or "").strip()
	if not existing_name:
		existing_rows = frappe.get_all(
			"Payment Entry",
			filters={
				"payment_type": "Pay",
				"party_type": "Customer",
				"party": sr.customer,
				"company": company,
				"reference_no": reference_no,
			},
			pluck="name",
			order_by="creation asc, name asc",
			limit_page_length=2,
		)
		if len(existing_rows) > 1:
			frappe.throw(
				_("Multiple refund Payment Entries already reference this Service Request. Reconcile them before retrying."),
				frappe.ValidationError,
			)
		existing_name = existing_rows[0] if existing_rows else ""

	if existing_name:
		if not frappe.db.exists("Payment Entry", existing_name):
			frappe.throw(
				_("The recorded advance refund Payment Entry is missing. Reconcile the Service Request before retrying."),
				frappe.ValidationError,
			)
		existing = frappe.get_doc("Payment Entry", existing_name)
		existing_amount = flt(existing.paid_amount or existing.received_amount)
		if explicit_amount and abs(existing_amount - refund_amount) > 0.01:
			frappe.throw(
				_("Advance refund {0} already exists for ₹{1}; a second refund cannot be created.").format(
					existing.name, existing_amount
				),
				frappe.ValidationError,
			)
		if cint(existing.docstatus) == 2:
			frappe.throw(
				_("Advance refund {0} is cancelled. Reconcile the cancelled entry before retrying.").format(existing.name),
				frappe.ValidationError,
			)
		if not sr.get("advance_refund_entry"):
			sr.db_set({
				"advance_refund_amount": existing_amount,
				"advance_refund_reason": reason or sr.get("advance_refund_reason") or "Not Repairable",
				"advance_refund_date": sr.get("advance_refund_date") or today(),
				"advance_refund_entry": existing.name,
			}, update_modified=False)
		return _advance_refund_result(existing, existing_amount, already_exists=True)

	if flt(sr.get("advance_refund_amount")) > 0:
		frappe.throw(
			_("This Service Request records an advance refund without a Payment Entry reference. Reconcile it before retrying."),
			frappe.ValidationError,
		)

	mode_of_payment = sr.advance_received_via or "Cash"

	# Map payment mode to ERPNext Mode of Payment
	mop_map = {"Cash": "Cash", "UPI": "Cash", "Card": "Cash", "Bank Transfer": "Bank Draft"}
	erp_mode = mop_map.get(mode_of_payment, "Cash")

	# Get default accounts
	company_account = frappe.db.get_value("Company", company, "default_cash_account") or \
		frappe.db.get_value("Company", company, "default_bank_account")

	if not company_account:
		frappe.throw(_("Please set default Cash or Bank account for company {0}").format(company), title=_("API Error"))

	try:
		from erpnext.accounts.party import get_party_account
		customer_account = get_party_account("Customer", sr.customer, company)
	except Exception:
		customer_account = frappe.db.get_value("Company", company, "default_receivable_account")

	if not customer_account:
		frappe.throw(_("Please set default Receivable account for company {0}").format(company), title=_("API Error"))

	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Pay"
	pe.party_type = "Customer"
	pe.party = sr.customer
	pe.company = company
	pe.mode_of_payment = erp_mode
	pe.paid_from = company_account
	pe.paid_from_account_currency = frappe.get_cached_value("Account", company_account, "account_currency")
	pe.paid_to = customer_account
	pe.paid_to_account_currency = frappe.get_cached_value("Account", customer_account, "account_currency")
	pe.paid_amount = refund_amount
	pe.received_amount = refund_amount
	pe.reference_no = reference_no
	pe.reference_date = today()
	pe.remarks = f"Advance refund for Service Request {sr.name}. Reason: {reason or 'Not Repairable'}"
	apply_cost_center(pe, warehouse=sr.source_warehouse)

	frappe.has_permission("Payment Entry", "create", throw=True)
	frappe.has_permission("Payment Entry", "read", throw=True)
	pe.insert()
	pe = _route_advance_refund_for_approval(pe)

	# Update Service Request
	sr.db_set("advance_refund_amount", refund_amount, update_modified=False)
	sr.db_set("advance_refund_reason", reason or "Not Repairable", update_modified=False)
	sr.db_set("advance_refund_date", today(), update_modified=False)
	sr.db_set("advance_refund_entry", pe.name, update_modified=False)

	message = _("Advance refund of ₹{0} posted: {1}") if pe.docstatus == 1 else _(
		"Advance refund of ₹{0} routed for approval: {1}"
	)
	frappe.msgprint(message.format(refund_amount, pe.name), indicator="green")

	return _advance_refund_result(pe, refund_amount)


# ── Inter-Store Service Transfer ─────────────────────────────────────

@frappe.whitelist()
def get_repair_destinations(service_request) -> list:
	"""Where this ticket's device may legitimately be sent for repair.

	Driven off the location hierarchy — CH Store — rather than the warehouse
	tree. A raw warehouse list offers 111 leaf bins on this company alone,
	including Damaged, Buyback, Demo and Supplier Returns, none of which is a
	place a technician works. Sending a customer's phone to a returns bin
	because it appeared in a dropdown is not a mistake worth allowing.

	Hubs are listed first when any store is flagged ``is_hub``; until that flag
	is configured every service-enabled store is offered, which is still correct
	— a store CAN repair for another store — just unranked.
	"""
	sr = assert_service_request_access(service_request, permission_type="read")
	current = sr.get("current_location") or sr.get("source_warehouse")

	filters = {"company": sr.company, "disabled": 0}
	if frappe.db.has_column("CH Store", "is_service_enabled"):
		filters["is_service_enabled"] = 1

	rows = frappe.get_all(
		"CH Store",
		filters=filters,
		fields=["name", "store_name", "warehouse", "city", "zone", "is_hub"],
		order_by="is_hub desc, store_name asc",
		limit_page_length=500,
	)
	return [
		{
			"store": row.name,
			"warehouse": row.warehouse,
			"label": row.store_name or row.name,
			"city": row.city,
			"zone": row.zone,
			"is_hub": cint(row.is_hub),
		}
		for row in rows
		if row.warehouse and row.warehouse != current
	]


@frappe.whitelist(methods=["POST"])
def create_service_transfer(service_request, to_store, reason=None) -> dict:
	"""Transfer a device from source store to a zone service center for repair.

	The submitted Stock Entry and logical movement are one database transaction.
	"""
	_require_store_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write", action="dispatch")

	if sr.transfer_status in ("In Transit", "Received at Service Center"):
		frappe.throw(_("Device is already in transfer (status: {0})").format(sr.transfer_status), title=_("API Error"))

	if not to_store:
		frappe.throw(_("Destination store/service center is required"), title=_("API Error"))
	from gofix.gofix_services.orchestration import _auto_create_device_transfer

	from_store = sr.get("current_location") or sr.source_warehouse
	transfer_name = _auto_create_device_transfer(sr, from_store, to_store, reason)

	sr.db_set("transferred_to_store", to_store, update_modified=True)
	sr.db_set("transfer_status", "In Transit", update_modified=False)
	sr.db_set("transfer_date", today(), update_modified=False)
	sr.db_set("transfer_reason", reason or "", update_modified=False)
	sr.db_set("last_transfer_reference", transfer_name, update_modified=False)

	# Parts follow the device (SAP parts-routing): repoint open spare
	# MRs/POs to the destination so deliveries land where the repair happens.
	from gofix.purchase_api import reroute_open_spare_procurement

	reroute_open_spare_procurement(sr, to_store)

	# Update current location
	sr.db_set("current_location", None, update_modified=False)  # In transit

	# Also update SO current_location if exists
	if sr.service_order:
		frappe.db.set_value("Sales Order", sr.service_order,
			"current_location", None, update_modified=False)

	frappe.msgprint(
		_("Device transfer initiated: {0} → {1}").format(from_store, to_store),
		indicator="blue")

	return {"status": "In Transit", "from": from_store, "to": to_store, "transfer": transfer_name}


@frappe.whitelist(methods=["POST"])
def receive_service_transfer(service_request) -> dict:
	"""Mark device as received at the destination service center."""
	_require_store_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write", action="receive")

	if sr.transfer_status != "In Transit":
		frappe.throw(_("Device is not in transit (status: {0})").format(sr.transfer_status), title=_("API Error"))

	# Goods receipt is a logistics act, not a service-desk one. The device's
	# stock sits in transit until someone at the destination actually takes
	# custody of it, so this refuses to claim receipt before that happened —
	# otherwise the ticket says "at the hub" while the ledger still says
	# "in transit", and the difference is a device nobody can find.
	_assert_device_movement_landed(sr)

	sr.db_set("transfer_status", "Received at Service Center", update_modified=True)
	sr.db_set("transfer_received_date", today(), update_modified=False)
	sr.db_set("current_location", sr.transferred_to_store, update_modified=False)

	if sr.service_order:
		frappe.db.set_value("Sales Order", sr.service_order,
			"current_location", sr.transferred_to_store, update_modified=False)

	frappe.msgprint(_("Device received at service center"), indicator="green")
	return {"status": "Received at Service Center"}


def _assert_device_movement_landed(sr) -> None:
	"""Refuse to mark a device received while its stock is still in transit.

	The dispatch leg moved the device out of the origin store into transit. The
	destination owns it only once the transit entry reaches Transferred, which
	is what the pickup/delivery flow produces. A ticket that skipped ahead would
	report the device at a bench it has not reached.

	Sites not running the transit states for service moves are left alone: with
	no transfer document to check, there is nothing to contradict.
	"""
	reference = sr.get("last_transfer_reference")
	if not reference or not frappe.db.exists("Stock Entry", reference):
		return

	status = (frappe.db.get_value("Stock Entry", reference, "custom_status") or "").strip()
	if status in ("Transferred", "Partially Transferred"):
		return

	frappe.throw(
		_("The device has not arrived yet — transfer {0} is at <b>{1}</b>. "
		  "Complete the pickup and delivery, and receive it at the destination, "
		  "before marking the ticket received.").format(reference, status or _("Draft")),
		title=_("Device Still In Transit"),
	)


# Transit states a dispatch can still be called back from, in the Stock Entry's
# own vocabulary — TRANSIT_STATUS_TRANSITIONS lets exactly these fall back to
# Draft, and "In Transit" onwards only moves forward. That is the real cut-off:
# once a driver has the consignment, recalling it is a logistics operation, not
# a click.
#
# These are NOT manifest statuses. An earlier version used Draft/Packed/Assigned
# from CH Transfer Manifest against this field, which no dispatch ever carries —
# every one starts at Pending With Goods — so cancel refused every single time.
_CANCELLABLE_TRANSIT_STATES = ("", "Draft", "Pending With Goods", "Ready For Pickup")


@frappe.whitelist(methods=["POST"])
def cancel_service_transfer(service_request, reason=None) -> dict:
	"""Call back a dispatch, any time before the destination takes the device on.

	The cut-off is physical receipt, not pickup. Until somebody at the far end
	has the device in their hands and has said so on the ticket, the origin
	still owns the decision — and a wrong destination is usually noticed while
	the van is moving, not before it leaves.

	What happens next depends on where the goods are, because the ledger has to
	match the world: a device still on the origin shelf never left, so the
	dispatch is torn up; a device already moving has to be physically brought
	back, so a return leg is raised and the ticket says it is coming home.
	"""
	_require_store_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write", action="cancel")

	if sr.transfer_status != "In Transit":
		frappe.throw(
			_("Only a dispatch the destination has not received yet can be cancelled "
			  "(status: {0}). Once the device has been taken on there, it comes back "
			  "as a return instead.").format(sr.transfer_status or _("not in transfer")),
			title=_("Nothing to Cancel"),
		)

	if not (reason or "").strip():
		frappe.throw(
			_("Give a reason — it is written to the ticket as the record of why "
			  "the dispatch was called back."),
			title=_("Reason Required"),
		)

	# The cut-off is physical receipt at the destination, not pickup. Up to that
	# moment nobody at the far end has taken the device on, so calling it back is
	# still a decision the origin gets to make. What the cancel DOES depends on
	# where the goods actually are:
	#
	#   still on the origin shelf  the dispatch is paperwork; tear it up
	#   already moving             something has to physically bring it home,
	#                              so raise the return leg and say so
	#
	# Pretending the second case never happened would put a movement in the
	# ledger that contradicts where the device is.
	reference = sr.get("last_transfer_reference")
	movement = ""
	if reference and frappe.db.exists("Stock Entry", reference):
		movement = (frappe.db.get_value("Stock Entry", reference, "custom_status") or "Draft").strip()

	if movement and movement not in _CANCELLABLE_TRANSIT_STATES:
		from gofix.gofix_services.orchestration import _auto_create_device_transfer

		return_ref = _auto_create_device_transfer(
			sr,
			sr.transferred_to_store,
			sr.source_warehouse,
			_("Dispatch called back: {0}").format(reason.strip()),
		)
		sr.db_set({
			"transfer_status": "Return In Transit",
			"current_location": None,
			"last_transfer_reference": return_ref,
			"transfer_reason": _("Called back — {0}").format(reason.strip()),
		}, update_modified=True)

		from gofix.purchase_api import reroute_open_spare_procurement

		reroute_open_spare_procurement(sr, sr.source_warehouse)
		sr.add_comment("Info", _("Dispatch called back mid-route: {0}").format(reason.strip()))
		frappe.msgprint(
			_("The device had already left, so it is being brought back on {0}. "
			  "Confirm its arrival at the store to close the loop.").format(return_ref),
			indicator="orange", title=_("Coming Back"),
		)
		return {"ok": True, "status": "Return In Transit", "transfer": return_ref,
		        "recalled_in_flight": True}

	if reference and movement:
		try:
			from ch_erp15.ch_erp15.custom.stock_entry import revert_transit_entry

			doc = frappe.get_doc("Stock Entry", reference)
			revert_transit_entry(doc)
			doc.db_set("custom_status", "Cancelled", update_modified=False)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"GoFix: could not revert transit for {reference}"
			)
			frappe.throw(
				_("The stock movement for {0} could not be reversed, so the dispatch "
				  "was left as it is. Resolve it in Stock before cancelling.").format(reference),
				title=_("Stock Not Reversed"),
			)

	destination = sr.transferred_to_store
	sr.db_set({
		"transfer_status": None,
		"transferred_to_store": None,
		"transfer_date": None,
		"transfer_reason": None,
		"last_transfer_reference": None,
		# The device never left, so it is where it always was.
		"current_location": sr.source_warehouse,
	}, update_modified=True)

	# Parts were redirected to the destination when the dispatch was raised;
	# they have to come back with it.
	from gofix.purchase_api import reroute_open_spare_procurement

	reroute_open_spare_procurement(sr, sr.source_warehouse)

	sr.add_comment(
		"Info",
		_("Dispatch to {0} cancelled before pickup — {1}").format(destination or "—", reason),
	)
	frappe.msgprint(
		_("Dispatch cancelled. The device stays at {0}.").format(
			(sr.source_warehouse or "").split(" - ")[0]
		),
		indicator="green",
	)
	return {"ok": True, "status": "", "current_location": sr.source_warehouse}


@frappe.whitelist(methods=["POST"])
def return_service_transfer(service_request) -> dict:
	"""Initiate return of device from service center back to origin store."""
	_require_store_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write", action="movement")

	if sr.transfer_status not in ("Received at Service Center", "Repair Complete"):
		frappe.throw(_("Device must be at service center to initiate return (status: {0})").format(
			sr.transfer_status))

	from gofix.gofix_services.orchestration import _auto_create_device_transfer

	from_store = sr.get("current_location") or sr.get("transferred_to_store")
	transfer_name = _auto_create_device_transfer(
		sr,
		from_store,
		sr.source_warehouse,
		_("Return to source store"),
	)
	sr.db_set("transfer_status", "Return In Transit", update_modified=True)
	sr.db_set("current_location", None, update_modified=False)
	sr.db_set("last_transfer_reference", transfer_name, update_modified=False)

	# Device is heading home — any spares still being procured should now
	# deliver to the origin store, not the hub the device is leaving.
	from gofix.purchase_api import reroute_open_spare_procurement

	reroute_open_spare_procurement(sr, sr.source_warehouse)

	frappe.msgprint(_("Return transfer initiated"), indicator="blue")
	return {"status": "Return In Transit", "transfer": transfer_name}


@frappe.whitelist(methods=["POST"])
def complete_service_transfer_return(service_request) -> dict:
	"""Mark device as returned to the origin store."""
	_require_store_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write", action="movement")

	if sr.transfer_status != "Return In Transit":
		frappe.throw(_("Device is not in return transit (status: {0})").format(sr.transfer_status), title=_("API Error"))

	sr.db_set("transfer_status", "Returned to Store", update_modified=True)
	sr.db_set("transfer_return_date", today(), update_modified=False)
	sr.db_set("current_location", sr.source_warehouse, update_modified=False)

	if sr.service_order:
		frappe.db.set_value("Sales Order", sr.service_order,
			"current_location", sr.source_warehouse, update_modified=False)

	frappe.msgprint(_("Device returned to origin store"), indicator="green")
	return {"status": "Returned to Store"}


# ── Pickup & Outstation Tracking ─────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def schedule_pickup(service_request, address, scheduled_datetime, agent=None) -> dict:
	"""Schedule a device pickup for outstation/courier mode service requests."""
	_require_sales_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write")

	address = _validate_logistics_address(address)
	scheduled_dt = _ensure_future_datetime(scheduled_datetime, _("Pickup schedule"))
	previous_schedule = sr.get("pickup_scheduled_datetime")

	sr.db_set("pickup_address", address, update_modified=True)
	sr.db_set("pickup_scheduled_datetime", scheduled_dt, update_modified=False)
	if agent:
		sr.db_set("pickup_agent", agent, update_modified=False)

	_audit_service_update(
		sr,
		"Pickup Scheduled",
		before=previous_schedule,
		after=scheduled_dt,
		remarks=f"Pickup scheduled for {sr.name}",
	)

	frappe.msgprint(_("Pickup scheduled"), indicator="blue")
	return {"message": "Pickup scheduled", "scheduled": str(scheduled_dt)}


@frappe.whitelist(methods=["POST"])
def complete_pickup(service_request) -> dict:
	"""Mark device pickup as completed."""
	_require_sales_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write")

	if not sr.get("pickup_scheduled_datetime"):
		frappe.throw(_("Pickup must be scheduled before it can be completed."), title=_("Pickup Not Scheduled"))
	if sr.get("pickup_completed_datetime"):
		return {"message": "Pickup already completed", "completed_at": str(sr.get("pickup_completed_datetime"))}

	completed_at = now()
	sr.db_set("pickup_completed_datetime", completed_at, update_modified=True)
	_audit_service_update(
		sr,
		"Pickup Completed",
		before=sr.get("pickup_scheduled_datetime"),
		after=completed_at,
		remarks=f"Pickup completed for {sr.name}",
	)
	frappe.msgprint(_("Pickup completed"), indicator="green")
	return {"message": "Pickup completed", "completed_at": completed_at}


@frappe.whitelist(methods=["POST"])
def dispatch_return(service_request, courier_name=None, tracking_number=None) -> dict:
	"""Dispatch repaired device back to customer via courier."""
	_require_sales_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write")

	mode_of_service = (sr.get("mode_of_service") or "").strip()
	if mode_of_service == "Courier":
		if not (courier_name or "").strip():
			frappe.throw(_("Return courier is required for courier-mode service requests."), title=_("Courier Required"))
		if not (tracking_number or "").strip():
			frappe.throw(_("Tracking number is required for courier dispatch."), title=_("Tracking Required"))

	sr.db_set("return_courier_name", (courier_name or "").strip(), update_modified=True)
	sr.db_set("return_tracking_number", (tracking_number or "").strip(), update_modified=False)
	sr.db_set("return_dispatched_date", today(), update_modified=False)
	_audit_service_update(
		sr,
		"Return Dispatched",
		before=mode_of_service or "Pending Dispatch",
		after=tracking_number or courier_name or "Hand Delivery",
		remarks=f"Return dispatched for {sr.name}",
	)

	frappe.msgprint(_("Device dispatched to customer"), indicator="blue")
	return {"message": "Dispatched", "tracking": tracking_number}


@frappe.whitelist(methods=["POST"])
def confirm_return_delivery(service_request) -> dict:
	"""Confirm customer received the returned device."""
	_require_sales_operation_role()
	sr = assert_service_request_access(service_request, permission_type="write")

	if sr.get("mode_of_service") == "Courier" and not sr.get("return_dispatched_date"):
		frappe.throw(_("Return must be dispatched before delivery can be confirmed."), title=_("Dispatch Pending"))

	sr.db_set("return_delivered_date", today(), update_modified=True)
	sr.db_set("decision", "Delivered", update_modified=False)
	_audit_service_update(
		sr,
		"Return Delivered",
		before=sr.get("return_dispatched_date") or "Dispatched",
		after=today(),
		remarks=f"Return delivery confirmed for {sr.name}",
	)

	frappe.msgprint(_("Return delivery confirmed"), indicator="green")
	return {"message": "Delivered"}


# ── Suggested Price Calculation ──────────────────────────────────────

@frappe.whitelist()
def calculate_suggested_price(service_order) -> dict:
	"""Calculate suggested repair price from technician hours and spare parts.

	Returns breakdown:
	  spare_parts_revenue, suggested_labor_cost, suggested_total,
	  actual_billed, price_override
	"""
	so = _get_scoped_service_order(service_order, "read")

	sr_name = so.service_request

	# Spare parts revenue
	parts = frappe.db.sql("""
		SELECT COALESCE(SUM(sales_price * qty_used), 0) as total_revenue
		FROM `tabSpare Parts Usage`
		WHERE service_request = %s
		  AND status = 'Active'
		  AND part_status IN ('Consumed', 'Issued')
	""", (sr_name,), as_dict=True)
	parts_revenue = flt(parts[0].total_revenue) if parts else 0

	# Labor from job sheets
	job_sheets = frappe.get_all("Job Assignment",
		filters={"service_order": service_order},
		fields=["actual_hours", "service_engineer"])
	employee_names = {row.service_engineer for row in job_sheets if row.service_engineer}
	has_hourly_rate = frappe.db.has_column("Employee", "custom_hourly_rate")
	employee_fields = ["name", "employee_name", "ctc"]
	if has_hourly_rate:
		employee_fields.append("custom_hourly_rate")
	employees = frappe.get_all(
		"Employee",
		filters={"name": ("in", tuple(employee_names))},
		fields=employee_fields,
		limit_page_length=len(employee_names),
	) if employee_names else []
	employee_by_name = {employee.name: employee for employee in employees}

	labor_details = []
	suggested_labor = 0
	for js in job_sheets:
		hours = flt(js.actual_hours)
		hourly_rate = 0
		employee = employee_by_name.get(js.service_engineer)
		engineer_name = (employee.employee_name if employee else None) or js.service_engineer or "Unassigned"
		if employee:
			hourly_rate = flt(employee.get("custom_hourly_rate")) if has_hourly_rate else 0
			if not hourly_rate:
				ctc = flt(employee.ctc)
				if ctc:
					hourly_rate = ctc / 2080

		line_total = hours * hourly_rate
		suggested_labor += line_total
		labor_details.append({
			"engineer": engineer_name,
			"hours": hours,
			"hourly_rate": hourly_rate,
			"line_total": line_total,
		})

	suggested_total = parts_revenue + suggested_labor
	actual_billed = flt(so.grand_total) or flt(so.total)
	price_override = actual_billed - suggested_total if suggested_total else 0

	return {
		"spare_parts_revenue": parts_revenue,
		"suggested_labor_cost": suggested_labor,
		"suggested_total": suggested_total,
		"actual_billed": actual_billed,
		"price_override": price_override,
		"labor_details": labor_details,
	}


def auto_close_service_order_after_billing(service_request=None, service_order=None) -> None:
	"""Bring a billed repair's Service Order to its terminal state.

	Wired into every invoice-creation path (SR auto-invoice, Ops Hub invoice,
	POS collect_repair_payment / close_repair_order) so an invoiced repair
	never lingers as a draft Sales Order with a QC-Pass badge. Runs as a
	system action (permission-exempt) but only when the repair is genuinely
	finished: all Job Assignments settled AND QC passed. Never raises —
	billing must not fail because closing hiccuped.
	"""

	try:
		so_name = service_order
		if not so_name and service_request:
			so_name = frappe.db.get_value("Service Request", service_request, "service_order")
		if not so_name:
			return
		so = frappe.get_doc("Sales Order", so_name)
		if not so.get("is_service_order"):
			return
		if so.docstatus == 2 or (so.docstatus == 1 and so.get("workflow_state") == "Closed"):
			return

		if so.get("qc_status") != "Pass":
			return

		jas = frappe.get_all(
			"Job Assignment",
			filters={"service_order": so_name, "docstatus": ["<", 2]},
			fields=["name", "assignment_status", "actual_hours"],
		)
		if not jas:
			return
		# QC has passed and the invoice exists — an assignment still open at
		# this point is stale (assigned but never worked, or left dangling by
		# rework churn). Settle it instead of blocking the close: worked hours
		# → Completed, untouched → Cancelled. No manual maker-step for
		# service closure.
		for ja in jas:
			if ja.assignment_status in ("Completed", "Closed", "Cancelled"):
				continue
			new_status = "Completed" if flt(ja.actual_hours) else "Cancelled"
			ja_doc = frappe.get_doc("Job Assignment", ja.name)
			ja_doc.check_permission("write")
			ja_doc.assignment_status = new_status
			ja_doc.flags.ignore_validate_update_after_submit = True
			ja_doc.save()

		if so.docstatus == 0:
			so.check_permission("submit")
			so.submit()
			# Submit-time hooks can knock qc/workflow back to Awaiting —
			# restore the QC verdict that gated this close.
			so.reload()

		updates = {"workflow_state": "Closed"}
		if so.get("qc_status") != "Pass":
			updates["qc_status"] = "Pass"
		so.db_set(updates, update_modified=False)
		so.reload()
		so.check_permission("write")
		so.update_status("Closed")
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"auto_close_service_order_after_billing failed: {service_request or service_order}",
		)


_SERVICE_BOARD_TABS = {
	"in_progress": ["Draft", "Accepted", "In Service", "In Progress", "On Hold"],
	"ready": ["Completed"],
	"invoiced": ["Invoiced"],
	"delivered": ["Delivered"],
}

# A device that has left the store for repair elsewhere is not simply "in
# progress" — nobody at this counter can act on it, and the customer asking
# after it needs a different answer. It is tracked by transfer_status rather
# than decision, so it is a filter in its own right rather than a tab entry.
_OUT_FOR_REPAIR_STATUSES = ("In Transit", "Received at Service Center", "Return In Transit")


@frappe.whitelist()
def get_store_service_board(warehouse, tab=None, search=None) -> dict:
	"""Service Tracker board for a store.

	Lists every Service Request ACCEPTED at this store (source_warehouse),
	regardless of where the device currently sits — a device shipped to a
	repair hub stays visible with its current location. Billing off-store
	devices still requires the customer-consent OTP (custody gate); this
	board only surfaces them.
	"""
	_require_store_operation_role()
	frappe.has_permission("Service Request", "read", throw=True)
	if not warehouse:
		return {"rows": [], "counts": {}}
	from gofix.scope_guard import assert_warehouse

	assert_warehouse(warehouse=warehouse)

	filters = {"source_warehouse": warehouse}
	if tab == "out_for_repair":
		filters["transfer_status"] = ["in", list(_OUT_FOR_REPAIR_STATUSES)]
	elif tab and tab in _SERVICE_BOARD_TABS:
		filters["decision"] = ["in", _SERVICE_BOARD_TABS[tab]]
	if tab == "ready":
		# "Ready to Bill" means billable — a Completed SR that already
		# carries an invoice (legacy status drift) is not billable again.
		filters["service_invoice"] = ["is", "not set"]

	or_filters = None
	if (search or "").strip():
		like = f"%{search.strip()}%"
		or_filters = [
			["name", "like", like],
			["contact_number", "like", like],
			["actual_imei", "like", like],
			["serial_no", "like", like],
			["customer_name", "like", like],
		]

	row_limit = min(get_int_setting("token_queue_limit", 200), 500)
	rows = frappe.get_list(
		"Service Request",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name", "decision", "customer", "customer_name",
			"contact_number", "serial_no", "actual_imei", "device_item_name",
			"issue_category", "service_date", "estimated_cost", "final_cost",
			"service_invoice", "transfer_status", "current_location",
			"transferred_to_store", "source_warehouse", "last_transfer_reference",
		],
		order_by="modified desc",
		limit_page_length=row_limit,
	)

	for r in rows:
		transfer = (r.get("transfer_status") or "").strip()
		if transfer in ("", "Not Transferred", "Returned to Store"):
			device_at = r.get("current_location") or warehouse
		elif transfer in ("In Transit", "Return In Transit"):
			device_at = None  # on the road
		else:
			device_at = r.get("current_location") or r.get("transferred_to_store")
		r["device_at"] = device_at
		r["at_home_store"] = bool(device_at == warehouse)
		r.update(device_movement_options(r, warehouse))

	decision_counts = {
		row.decision: cint(row.count)
		for row in frappe.get_list(
			"Service Request",
			filters={"source_warehouse": warehouse},
			fields=["decision", {"COUNT": "name", "as": "count"}],
			group_by="decision",
			limit_page_length=len(_SERVICE_BOARD_TABS) + 10,
		)
	}
	for row in rows:
		row["status"] = row.decision
	counts = {
		key: sum(cint(decision_counts.get(s, 0)) for s in statuses)
		for key, statuses in _SERVICE_BOARD_TABS.items()
	}
	counts["all"] = sum(cint(v) for v in decision_counts.values())
	counts["out_for_repair"] = cint(frappe.db.count(
		"Service Request",
		{"source_warehouse": warehouse, "transfer_status": ["in", list(_OUT_FOR_REPAIR_STATUSES)]},
	))

	return {"rows": rows, "counts": counts}


# Landed states in the Stock Entry's own transit vocabulary: the consignment
# is at the destination and can be taken into stock there.
_LANDED_TRANSIT_STATES = ("Ready For Receive", "Receive At Transit", "Transferred",
                          "Partially Transferred")


def device_movement_options(row, home_warehouse=None) -> dict:
	"""Which device movements this ticket can actually perform right now.

	The tracker offered Send to Hub and Cancel Dispatch and nothing else, so a
	device that had left its store had no way back: cancel stops being legal the
	moment a driver picks it up, receive lives only in the Ops Hub, and the
	return leg was reachable only from a status the ticket could not get to
	without receiving first. The device could go out and not come home.

	Both halves of the answer are needed, because the ticket and the ledger
	disagree more often than they agree — a ticket reads "In Transit" from the
	moment it is dispatched, while the goods sit on the origin's dock until
	somebody drives them. So the ticket's transfer_status says which leg we are
	on, and the transfer document says how far that leg has actually got.
	"""
	transfer = (row.get("transfer_status") or "").strip()
	reference = row.get("last_transfer_reference")
	movement = ""
	if reference:
		movement = (frappe.db.get_value("Stock Entry", reference, "custom_status") or "").strip()

	# No transfer document means nothing contradicts the ticket, so trust it.
	landed = (not reference) or movement in _LANDED_TRANSIT_STATES
	actions = []
	if transfer in ("", "Not Transferred", "Returned to Store"):
		actions.append("dispatch")
	elif transfer == "In Transit":
		# Callable back right up to physical receipt: until somebody at the far
		# end takes the device on, the origin still gets to change its mind.
		actions.append("cancel")
		if landed:
			actions.append("receive")
	elif transfer in ("Received at Service Center", "Repair Complete"):
		actions.append("return")
	elif transfer == "Return In Transit":
		if landed:
			actions.append("confirm_return")

	return {
		"movement_status": movement,
		"transfer_actions": actions,
		"awaiting_pickup": transfer == "In Transit" and movement in _CANCELLABLE_TRANSIT_STATES,
		"home_warehouse": home_warehouse or row.get("source_warehouse"),
	}


@frappe.whitelist(methods=["POST"])
def add_ticket_note(service_request, note, visibility="Internal") -> dict:
	"""Add a note to a ticket — from anywhere, at any point in its life.

	The one thing custody does not gate. A note claims nothing about where the
	device is or what has been done to it; it is somebody writing down what they
	know, and the moments you most want that written down are exactly the ones
	the rest of the ticket is frozen for — the customer rang while the phone was
	on a van, the hub called about a part.

	Appended rather than assigned, and stamped with who and when, because "any
	time by anyone" means two people will eventually write at once and neither
	should silently overwrite the other.
	"""
	sr = assert_service_request_access(service_request, permission_type="write", action="note")

	note = (note or "").strip()
	if not note:
		frappe.throw(_("Write something first."), title=_("Empty Note"))
	limit = get_int_setting("ticket_note_max_chars", 2000)
	if len(note) > limit:
		frappe.throw(
			_("Keep the note under {0} characters.").format(limit), title=_("Note Too Long")
		)

	field = "customer_remarks" if visibility == "Customer" else "internal_remarks"
	if not sr.meta.get_field(field):
		frappe.throw(_("This site has no {0} field.").format(field))

	stamp = _("{0} · {1}").format(
		frappe.utils.get_fullname(frappe.session.user),
		frappe.utils.format_datetime(now_datetime(), "medium"),
	)
	existing = (sr.get(field) or "").strip()
	updated = f"{existing}\n\n{stamp}\n{note}".strip() if existing else f"{stamp}\n{note}"
	sr.db_set(field, updated, update_modified=True)

	# The field holds the running text; the comment is the audit trail, which
	# nobody can edit away.
	sr.add_comment("Comment", note)
	return {"ok": True, "field": field, "value": updated}


@frappe.whitelist()
def get_device_movement_options(service_request) -> dict:
	"""The same answer for one ticket, for a screen that holds a single card."""
	sr = assert_service_request_access(service_request, permission_type="read")
	return device_movement_options(
		{
			"transfer_status": sr.get("transfer_status"),
			"last_transfer_reference": sr.get("last_transfer_reference"),
			"source_warehouse": sr.get("source_warehouse"),
		},
		sr.get("source_warehouse"),
	)


# ── Issue → Solution → Spare Cascade APIs ────────────────────────────

@frappe.whitelist()
def get_solutions_for_issues(issue_categories) -> list:
	"""Return active Repair Solutions for the given issue categories.
	Args:
		issue_categories: JSON list of Issue Category names
	"""
	_require_reference_read(
		_("view repair solutions"),
		("Issue Category", "Repair Solution"),
	)
	issue_categories = _bounded_name_list(issue_categories, _("Issue categories"))

	if not issue_categories:
		return []

	return frappe.get_list("Repair Solution",
		filters={
			"issue_category": ["in", issue_categories],
			"is_active": 1
		},
		fields=["name", "solution_name", "issue_category", "solution_code",
				"estimated_minutes", "requires_spare", "skill_level", "minimum_grade"],
		order_by="issue_category, solution_name",
		limit_page_length=get_int_setting("token_queue_limit", 200),
	)


@frappe.whitelist()
def get_spares_for_solution(repair_solution, device_item=None) -> list:
	"""Return active mapped spares for a given Repair Solution.

	When ``device_item`` is passed, spares that fail the device applicability
	ladder (brand / category / model fitment) are filtered out."""
	if not repair_solution:
		return []
	_require_reference_read(
		_("view mapped repair spares"),
		("Repair Solution", "Solution Spare Mapping", "Item"),
	)
	frappe.has_permission("Repair Solution", "read", repair_solution, throw=True)
	if device_item:
		frappe.has_permission("Item", "read", device_item, throw=True)

	rows = frappe.get_list("Solution Spare Mapping",
		filters={
			"repair_solution": repair_solution,
			"is_active": 1
		},
		fields=["name", "spare_item", "item_name", "default_qty", "uom", "is_mandatory"],
		order_by="is_mandatory desc, item_name",
		limit_page_length=get_int_setting("token_queue_limit", 200),
	)
	if device_item:
		rows = [r for r in rows if is_spare_compatible_with_device(r.spare_item, device_item)]
		if not rows:
			rows = _suggest_spares_for_solution(repair_solution, device_item)
	return rows


@frappe.whitelist()
def get_spares_for_solutions(repair_solutions, device_item=None) -> list:
	"""Return active mapped spares for multiple Repair Solutions.
	Args:
		repair_solutions: JSON list of Repair Solution names
		device_item: optional — drop spares that fail the device
			applicability ladder (brand / category / model fitment)
	"""
	_require_reference_read(
		_("view mapped repair spares"),
		("Repair Solution", "Solution Spare Mapping", "Item"),
	)
	repair_solutions = _bounded_name_list(repair_solutions, _("Repair solutions"))

	if not repair_solutions:
		return []
	if device_item:
		frappe.has_permission("Item", "read", device_item, throw=True)

	rows = frappe.get_list("Solution Spare Mapping",
		filters={
			"repair_solution": ["in", repair_solutions],
			"is_active": 1
		},
		fields=["name", "repair_solution", "spare_item", "item_name",
				"default_qty", "uom", "is_mandatory"],
		order_by="repair_solution, is_mandatory desc, item_name",
		limit_page_length=get_int_setting("token_queue_limit", 200),
	)
	if device_item:
		rows = [r for r in rows if is_spare_compatible_with_device(r.spare_item, device_item)]
		covered = {r.repair_solution for r in rows}
		for sol in repair_solutions:
			if sol not in covered:
				rows.extend(_suggest_spares_for_solution(sol, device_item))
	return rows


@frappe.whitelist()
def get_eligible_technicians(issue_categories, warehouse=None) -> list:
	"""Return technicians (Employees) whose Technician Grade covers all given issues.
	Args:
		issue_categories: JSON list of Issue Category names
		warehouse: optional warehouse to filter by default_shift location
	"""
	_require_reference_read(
		_("view eligible technicians"),
		("Employee", "Technician Grade", "Repair Solution"),
		role_field="job_assignment_creation_roles",
	)
	issue_categories = _bounded_name_list(issue_categories, _("Issue categories"))
	if warehouse:
		from gofix.scope_guard import assert_warehouse

		assert_warehouse(warehouse=warehouse)

	if not issue_categories:
		return []

	# Get minimum skill requirements from Repair Solution masters
	required_skills = {}
	solutions = frappe.get_list("Repair Solution",
		filters={"issue_category": ["in", issue_categories], "is_active": 1},
		fields=["issue_category", "skill_level"],
		limit_page_length=get_int_setting("token_queue_limit", 200),
	)
	skill_order = {"Basic": 1, "Intermediate": 2, "Advanced": 3, "Expert": 4}
	for s in solutions:
		cat = s.issue_category
		level = skill_order.get(s.skill_level, 1)
		if cat not in required_skills or level > required_skills[cat]:
			required_skills[cat] = level

	# Find all grades whose skills cover the required categories at the right level
	row_limit = get_int_setting("token_queue_limit", 200)
	grades = frappe.get_list(
		"Technician Grade",
		filters={"is_active": 1},
		fields=["name", "grade_level"],
		limit_page_length=row_limit,
	)
	grade_names = tuple(grade.name for grade in grades)
	skill_rows = frappe.get_all(
		"Technician Skill",
		filters={
			"parent": ("in", grade_names),
			"issue_category": ("in", tuple(issue_categories)),
		},
		fields=["parent", "issue_category", "max_skill_level"],
		limit_page_length=row_limit * max(1, len(issue_categories)),
	) if grade_names else []
	skills_by_grade = {}
	for skill in skill_rows:
		skills_by_grade.setdefault(skill.parent, {})[skill.issue_category] = skill_order.get(
			skill.max_skill_level, 1
		)
	eligible_grades = []
	for grade in grades:
		skill_map = skills_by_grade.get(grade.name, {})
		covers_all = True
		for cat, req_level in required_skills.items():
			if cat not in skill_map or skill_map[cat] < req_level:
				covers_all = False
				break
		if covers_all:
			eligible_grades.append(grade.name)

	if not eligible_grades:
		return []

	# Find employees with these grades — technicians based at the repair
	# location first; fall back to all graded technicians when the location
	# has no dedicated staff (rollout-friendly).
	filters = {"technician_grade": ["in", eligible_grades], "status": "Active"}
	rows = frappe.get_list("Employee",
		filters=filters,
		fields=["name", "employee_name", "technician_grade", "designation",
			"gofix_service_warehouse" if frappe.db.has_column("Employee", "gofix_service_warehouse") else "designation"],
		order_by="employee_name",
		limit_page_length=row_limit,
	)
	# A lapsed authorisation is not a qualification. Skip technicians whose
	# certification has run out, so routing cannot hand brand-authorised work
	# to someone no longer approved to do it. A blank expiry means no
	# time-limited authorisation applies and the technician stays eligible.
	from gofix.service_maturity import certification_valid

	rows = [r for r in rows if certification_valid(r.name)]

	# Same roster rule as the picker — one answer to "who works here".
	rostered = technicians_mapped_to_location(warehouse)
	if rostered:
		on_roster = [r for r in rows if r.name in rostered]
		if on_roster:
			return on_roster

	if warehouse and frappe.db.has_column("Employee", "gofix_service_warehouse"):
		local = [r for r in rows if r.get("gofix_service_warehouse") == warehouse]
		if local:
			return local
	return rows


@frappe.whitelist()
def get_reopenable_repairs(serial_no=None, service_request=None, company=None) -> list:
	"""Past repairs on a device, with the individual work lines that can fail.

	A returning customer says "it's doing it again", not "solution line 3 of
	SR-260828-16218". This gives the counter the list to point at: each
	delivered repair, what was done on it, and whether the workmanship warranty
	on that repair is still live.
	"""
	serial_no = (serial_no or "").strip()
	if not serial_no and service_request:
		serial_no = frappe.db.get_value("Service Request", service_request, "serial_no")
	if not serial_no:
		return []

	filters = {
		"serial_no": serial_no,
		"docstatus": 1,
		"decision": ("in", ("Completed", "Invoiced", "Delivered")),
	}
	if company:
		filters["company"] = company

	today_ = getdate(nowdate())
	out = []
	for sr in frappe.get_all(
		"Service Request", filters=filters,
		fields=["name", "service_date", "repair_warranty_expiry", "customer_name"],
		order_by="service_date desc", limit_page_length=10,
	):
		covered = bool(sr.repair_warranty_expiry and getdate(sr.repair_warranty_expiry) >= today_)
		lines = frappe.get_all(
			"SR Solution Line",
			filters={"parent": sr.name, "parenttype": "Service Request",
			         "status": ("in", ("Completed", "Skipped"))},
			fields=["name", "repair_solution", "issue_category", "status", "technician"],
		)
		if not lines:
			continue
		out.append({
			"service_request": sr.name,
			"service_date": str(sr.service_date or ""),
			"under_warranty": covered,
			"warranty_expiry": str(sr.repair_warranty_expiry or ""),
			"solutions": [
				{
					"solution_line": l.name,
					"repair_solution": l.repair_solution,
					"issue_category": l.issue_category,
					"technician": l.technician,
					"status": l.status,
				}
				for l in lines
			],
		})
	return out


@frappe.whitelist(methods=["POST"])
def reopen_repair(solution_line, description=None, company=None) -> dict:
	"""Raise a return visit against the exact repair line that failed.

	A new ticket rather than reviving the old one: the first repair was
	delivered and invoiced, and rewriting a closed job loses what actually
	happened. The link back is what carries the meaning — the new ticket knows
	which repair and which line it is a comeback for, so warranty pricing,
	first-time-fix and any supplier claim all resolve to the right thing.
	"""
	_require_store_operation_role()

	line = frappe.db.get_value(
		"SR Solution Line", solution_line,
		["name", "parent", "repair_solution", "issue_category"], as_dict=True,
	)
	if not line:
		frappe.throw(_("That repair line no longer exists."), title=_("Nothing to Reopen"))

	old = assert_service_request_access(line.parent, permission_type="read")

	sr = frappe.new_doc("Service Request")
	for field in ("customer", "customer_name", "contact_number", "device_item",
	              "device_item_name", "serial_no", "actual_imei", "brand", "company",
	              "source_warehouse", "email", "accessories_received",
	              "product_condition_desc", "backup_info"):
		if old.get(field):
			sr.set(field, old.get(field))
	sr.issue_category = line.issue_category
	sr.issue_description = description or _(
		"Return visit — {0} carried out on {1} has not held."
	).format(line.repair_solution, old.name)
	sr.service_date = nowdate()
	sr.decision = "Draft"
	sr.warranty_status = old.get("warranty_status") or "Out of Warranty"
	sr.device_condition = old.get("device_condition") or "Damaged"
	sr.data_backup_disclaimer = 1
	# Submission gates on these two, and a return visit that cannot be submitted
	# is a return visit the counter cannot log. Carry the original's words over;
	# fall back to a statement of fact rather than leaving them blank.
	sr.product_condition_desc = sr.product_condition_desc or _(
		"Returned by customer after repair {0}."
	).format(old.name)
	sr.backup_info = sr.backup_info or _("Carried over from {0}.").format(old.name)
	sr.previous_service_request = old.name
	if sr.meta.get_field("is_repeat_complaint"):
		sr.is_repeat_complaint = 1

	sr.append("issue_lines", {
		"issue_category": line.issue_category,
		"reported_by": "Customer",
		"status": "Open",
		"description": sr.issue_description,
		"reopened_from_solution": line.name,
		"reopened_from_request": old.name,
	})
	sr.flags.ignore_mandatory = True
	sr.insert(ignore_permissions=True)
	sr.submit()

	frappe.get_doc("Service Request", old.name).add_comment(
		"Info",
		_("Customer returned about {0}; reopened as {1}.").format(
			line.repair_solution,
			f'<a href="/app/service-request/{sr.name}">{sr.name}</a>',
		),
	)
	return {
		"ok": True,
		"service_request": sr.name,
		"reopened_from": old.name,
		"solution": line.repair_solution,
	}


def technicians_mapped_to_location(warehouse: str) -> set:
	"""Employees whose USER is rostered to this store in CH User Scope.

	POS already answers "who works here" from CH User Scope Store, and that
	roster is what an administrator actually maintains. GoFix was answering the
	same question from Employee.gofix_service_warehouse — a second, parallel
	mapping nobody keeps in step, which is how the Ops Hub ended up offering
	technicians from other stores.

	Returns an empty set when the store has no roster, which callers treat as
	"no opinion" and fall back to the old field. Better an unfiltered picker
	than an empty one: a site that has not adopted CH User Scope must still be
	able to assign work.
	"""
	if not warehouse:
		return set()
	try:
		from ch_erp15.ch_erp15.scope import get_store_roster
	except ImportError:
		return set()

	store = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "name")
	if not store:
		node, seen = warehouse, set()
		while node and node not in seen:
			seen.add(node)
			store = frappe.db.get_value("CH Store", {"warehouse_group": node}, "name")
			if store:
				break
			node = frappe.db.get_value("Warehouse", node, "parent_warehouse")
	if not store:
		return set()

	# One roster for the whole bench — the same list the POS closure screen
	# reads, which is the point: the two must never disagree about who works
	# here. It unions CH User Scope with the configured assignment records.
	users = [row["user"] for row in (get_store_roster(store) or []) if row.get("user")]

	# POS Profile users are staff on a till at this warehouse. Kept as a second
	# read because a profile can point at a warehouse whose CH Store is not the
	# one resolved above, and a technician missing from the picker is worse than
	# one extra name in it.
	users += frappe.db.sql_list(
		"""
		SELECT DISTINCT pu.user
		FROM `tabPOS Profile User` pu
		JOIN `tabPOS Profile` p ON p.name = pu.parent
		WHERE p.warehouse = %s AND IFNULL(p.disabled, 0) = 0
		""",
		warehouse,
	)
	users = list(dict.fromkeys(users))
	if not users:
		return set()

	return set(frappe.get_all(
		"Employee",
		filters={"user_id": ("in", users), "status": "Active"},
		pluck="name",
	))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def technician_query(doctype, txt, searchfield, start, page_len, filters) -> list:
	"""Link-field query for technician pickers — repair-location aware.

	filters.sr_name — resolve the ticket's EFFECTIVE repair warehouse (hub
	when transferred) and rank its technicians first; when the location has
	dedicated technicians, only they are shown. Only Active employees with a
	Technician Grade appear.
	"""
	_require_reference_read(
		_("search eligible technicians"),
		("Employee", "Technician Grade"),
		role_field="job_assignment_creation_roles",
	)
	if isinstance(filters, str):
		filters = json.loads(filters)
	filters = filters or {}

	warehouse = filters.get("warehouse")
	company = filters.get("company")
	if warehouse:
		from gofix.scope_guard import assert_warehouse

		assert_warehouse(warehouse=warehouse)
	if filters.get("sr_name"):
		from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
			_effective_repair_warehouse,
		)

		sr = assert_service_request_access(filters["sr_name"], permission_type="read")
		company = company or sr.company
		if not warehouse:
			warehouse = _effective_repair_warehouse(sr)

	has_wh_col = frappe.db.has_column("Employee", "gofix_service_warehouse")
	values = {
		"txt": f"%{txt}%" if txt else "%",
		"start": cint(start),
		"page_len": min(
			max(cint(page_len) or 20, 1),
			min(get_int_setting("technician_candidate_limit", 200), 500),
		),
		"warehouse": warehouse or "",
	}
	# A technician belongs to a company before they belong to a store. Without
	# this the picker offered GoGizmo's staff on a GoFix ticket whenever the
	# store had no roster — the two payrolls are separate and must stay so.
	company_clause = ""
	if company:
		company_clause = "AND e.company = %(company)s"
		values["company"] = company
	wh_rank = (
		"CASE WHEN e.gofix_service_warehouse = %(warehouse)s THEN 0 "
		"WHEN IFNULL(e.gofix_service_warehouse, '') = '' THEN 1 ELSE 2 END"
		if has_wh_col and warehouse
		else "0"
	)
	rows = frappe.db.sql(
		f"""
		SELECT e.name, e.employee_name, e.technician_grade,
		       {wh_rank} AS wh_rank
		FROM `tabEmployee` e
		WHERE e.status = 'Active'
		  AND IFNULL(e.technician_grade, '') != ''
		  AND (e.name LIKE %(txt)s OR e.employee_name LIKE %(txt)s)
		  {company_clause}
		ORDER BY wh_rank, e.employee_name
		LIMIT %(start)s, %(page_len)s
		""",
		values,
	)
	# The store roster wins where one exists: a technician offered for this
	# location must be somebody actually rostered to it, not merely tagged to it
	# on a second field.
	rostered = technicians_mapped_to_location(warehouse)
	if rostered:
		on_roster = [r for r in rows if r[0] in rostered]
		if on_roster:
			return [r[:3] for r in on_roster]

	# No roster, or nobody on it holds a technician grade — fall back to the
	# warehouse tag rather than returning nothing.
	if has_wh_col and warehouse and any(r[3] == 0 for r in rows):
		rows = [r for r in rows if r[3] == 0]
	return [r[:3] for r in rows]


# Keyword profiles per solution-code prefix — used to SUGGEST compatible
# spares from the live catalogue when no explicit Solution Spare Mapping
# exists yet (longest prefix wins).
_SPARE_SUGGESTION_KEYWORDS = {
	"SCR": ["display", "screen", "touch", "glass", "lcd", "folder"],
	"BAT": ["battery"],
	"CHG": ["charg", "port", "adapter", "power"],
	"CAM": ["camera", "lens"],
	"AUD-SPK": ["speaker"],
	"AUD-MIC": ["mic"],
	"AUD": ["speaker", "mic", "audio", "earpiece", "ringer"],
	"BTN-KBD": ["keyboard"],
	"BTN-TPD": ["touchpad", "trackpad"],
	"BTN": ["button", "flex", "volume", "power"],
	"PHY-BCK": ["back panel", "back glass", "housing", "back cover"],
	"PHY-STR": ["strap", "band"],
	"PHY-HNG": ["hinge"],
	"PHY": ["housing", "panel", "frame", "body"],
	"SNS-FPR": ["fingerprint"],
	"SNS": ["sensor"],
	"NET": ["wifi", "antenna", "network", "bluetooth"],
}


def _suggest_spares_for_solution(repair_solution, device_item, limit=8) -> list:
	"""Device-compatible catalogue spares matching the solution's keyword
	profile — returned with ``suggested=1`` so the UI can distinguish them
	from explicit BOM mappings."""
	code = frappe.db.get_value("Repair Solution", repair_solution, "solution_code") or ""
	keywords = None
	for prefix in sorted(_SPARE_SUGGESTION_KEYWORDS, key=len, reverse=True):
		if code.startswith(prefix):
			keywords = _SPARE_SUGGESTION_KEYWORDS[prefix]
			break
	if not keywords:
		return []

	like = " OR ".join(f"i.item_name LIKE %(kw{n})s" for n in range(len(keywords)))
	values = {f"kw{n}": f"%{kw}%" for n, kw in enumerate(keywords)}
	values["limit"] = limit * 4
	candidates = frappe.db.sql(
		f"""
		SELECT i.name, i.item_name, i.stock_uom
		FROM `tabItem` i
		WHERE i.disabled = 0 AND i.has_variants = 0 AND i.is_stock_item = 1
		  AND i.item_group = 'Spares' AND ({like})
		ORDER BY i.item_name
		LIMIT %(limit)s
		""",
		values,
		as_dict=True,
	)
	out = []
	for c in candidates:
		if not is_spare_compatible_with_device(c.name, device_item):
			continue
		out.append(
			{
				"name": None,
				"repair_solution": repair_solution,
				"spare_item": c.name,
				"item_name": c.item_name,
				"default_qty": 1,
				"uom": c.stock_uom or "Nos",
				"is_mandatory": 0,
				"suggested": 1,
			}
		)
		if len(out) >= limit:
			break
	return out


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_mapped_spare_items(doctype, txt, searchfield, start, page_len, filters) -> list:
	"""Server-side query for Link field: returns Items mapped to a Repair Solution.
	Used as 'query' in spare_item get_query on SR Spare Line.
	"""
	_require_reference_read(
		_("search mapped repair spares"),
		("Solution Spare Mapping", "Item"),
	)
	repair_solution = filters.get("repair_solution")
	if not repair_solution:
		return []

	mapped = frappe.get_list("Solution Spare Mapping",
		filters={"repair_solution": repair_solution, "is_active": 1},
		pluck="spare_item"
	)
	if not mapped:
		return []

	return frappe.get_list("Item",
		filters=[
			["name", "in", mapped],
			["name", "like", f"%{txt}%"] if txt else ["name", "in", mapped]
		],
		fields=["name", "item_name"],
		as_list=True,
		limit_page_length=min(max(cint(page_len) or 20, 1), get_int_setting("token_queue_limit", 200)),
		limit_start=start
	)


# ── Spare ↔ Device Model compatibility ─────────────────────────────────────
# A spare Item can list the device models it fits via the "Compatible Device
# Models" child table (custom field `gofix_compatible_models`). Semantics:
#   • empty list   → universal spare, fits any device
#   • non-empty    → spare is restricted to the listed device models only
# This lets the repair flow only offer spares that match the device being
# repaired, so technicians cannot pick the wrong part.

def _device_brand_and_category(item_code):
	"""Brand + CH Category of a device item, falling back to its variant template."""
	row = frappe.db.get_value("Item", item_code, ["brand", "ch_category", "variant_of"], as_dict=True)
	if not row:
		return None, None
	brand, category = row.brand, row.ch_category
	if row.variant_of and (not brand or not category):
		t = frappe.db.get_value("Item", row.variant_of, ["brand", "ch_category"], as_dict=True)
		if t:
			brand = brand or t.brand
			category = category or t.ch_category
	return brand, category


def is_spare_compatible_with_device(spare_item, device_item) -> bool:
	"""Return True if `spare_item` may be used on `device_item`.

	Market-standard applicability ladder (most specific tier wins):
	  1. Universal spare / consumable flag        → always compatible.
	  2. Explicit compatible-model rows (fitment) → device must be listed
	     (the same spare may list many models — interchangeability).
	  3. Category tier — the spare's CH Category declares which device
	     category it serves (Laptop Spares → Laptops): a laptop spare never
	     fits a mobile.
	  4. Brand tier — a branded spare only fits devices of the same brand
	     (Apple spare never fits a Samsung); unbranded spares are
	     brand-agnostic.
	"""
	if not spare_item or not device_item:
		return True

	spare = frappe.db.get_value(
		"Item", spare_item, ["brand", "ch_category", "gofix_universal_spare"], as_dict=True
	)
	if spare and cint(spare.gofix_universal_spare):
		return True

	has_any = frappe.db.exists(
		"GoFix Spare Compatible Model",
		{"parent": spare_item, "parenttype": "Item"},
	)
	if has_any:
		return bool(frappe.db.exists(
			"GoFix Spare Compatible Model",
			{"parent": spare_item, "parenttype": "Item", "device_model": device_item},
		))

	device_brand, device_category = _device_brand_and_category(device_item)

	# Category tier — only enforced when both sides are resolvable.
	if spare and spare.ch_category and device_category:
		serves = frappe.db.get_value("CH Category", spare.ch_category, "gofix_spares_for_category")
		if serves and serves != device_category:
			return False

	# Brand tier.
	if spare and spare.brand and device_brand and spare.brand.strip().lower() != device_brand.strip().lower():
		return False

	return True


@frappe.whitelist()
def check_spare_compatibility(spare_item, device_item) -> dict:
	"""Whitelisted wrapper so the client can verify compatibility before adding."""
	_require_reference_read(_("check spare compatibility"), ("Item",))
	frappe.has_permission("Item", "read", spare_item, throw=True)
	frappe.has_permission("Item", "read", device_item, throw=True)
	return {"compatible": is_spare_compatible_with_device(spare_item, device_item)}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_compatible_spare_items(doctype, txt, searchfield, start, page_len, filters) -> list:
	"""Link-field query: return spare Items compatible with a given device.

	Used as the `query` on spare-part Link fields in the repair flow. Filters:
		device_item  — restrict to spares that fit this device (or are universal)
		item_group   — restrict to a spare Item Group (descendants, inclusive)
	"""
	_require_reference_read(
		_("search compatible repair spares"),
		("Solution Spare Mapping", "Item"),
	)
	if isinstance(filters, str):
		filters = json.loads(filters)
	filters = filters or {}

	device_item = filters.get("device_item")
	item_group = filters.get("item_group")
	solutions = filters.get("solutions") or []
	issue_categories = filters.get("issue_categories") or []
	if isinstance(solutions, str):
		solutions = json.loads(solutions)
	if isinstance(issue_categories, str):
		issue_categories = json.loads(issue_categories)

	conditions = ["i.has_variants = 0", "i.disabled = 0"]
	values = {
		"txt": f"%{txt}%" if txt else "%",
		"start": cint(start),
		"page_len": min(
			max(cint(page_len) or 20, 1),
			get_int_setting("token_queue_limit", 200),
		),
	}
	conditions.append("(i.name LIKE %(txt)s OR i.item_name LIKE %(txt)s)")

	# Scope to the solution being worked on: if ops has mapped spares for the
	# active solution(s) (Solution Spare Mapping), only those + universal
	# consumables show. No mappings yet → fall back to the device ladder so an
	# unmapped catalogue doesn't brick the picker.
	if solutions or issue_categories:
		mapping_or = []
		if solutions:
			mapping_or.append({"repair_solution": ("in", solutions)})
		if issue_categories:
			mapping_or.append({"issue_category": ("in", issue_categories)})
		mapped = frappe.get_all(
			"Solution Spare Mapping",
			filters={"is_active": 1},
			or_filters=mapping_or,
			pluck="spare_item",
		)
		if mapped:
			universal = (
				"i.gofix_universal_spare = 1"
				if frappe.db.has_column("Item", "gofix_universal_spare")
				else "1 = 0"
			)
			conditions.append(f"(i.name IN %(mapped_spares)s OR {universal})")
			values["mapped_spares"] = tuple(set(mapped))

	group_join = ""
	if item_group:
		node = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
		if not node:
			# Group naming drifts between environments ("Spare Parts" vs
			# "Spares") — fall back to the closest spare-ish group instead of
			# silently dropping the filter and flooding the picker with the
			# whole catalogue.
			for candidate in ("Spares", "Spare Parts"):
				node = frappe.db.get_value("Item Group", candidate, ["lft", "rgt"], as_dict=True)
				if node:
					break
			if not node:
				alt = frappe.db.get_value(
					"Item Group", {"name": ("like", "%spare%")}, ["lft", "rgt"], as_dict=True
				)
				node = alt
		if node:
			group_join = (
				"JOIN `tabItem Group` ig ON ig.name = i.item_group "
				"AND ig.lft >= %(lft)s AND ig.rgt <= %(rgt)s"
			)
			values["lft"] = node.lft
			values["rgt"] = node.rgt

	if device_item:
		values["device_item"] = device_item
		device_brand, device_category = _device_brand_and_category(device_item)
		values["device_brand"] = device_brand or ""
		values["device_category"] = device_category or ""
		has_universal_col = frappe.db.has_column("Item", "gofix_universal_spare")
		has_serves_col = frappe.db.has_column("CH Category", "gofix_spares_for_category")
		universal_clause = "i.gofix_universal_spare = 1" if has_universal_col else "1 = 0"
		category_clause = (
			"""(
				%(device_category)s = '' OR i.ch_category IS NULL OR i.ch_category = ''
				OR NOT EXISTS (
					SELECT 1 FROM `tabCH Category` sc
					WHERE sc.name = i.ch_category
					  AND IFNULL(sc.gofix_spares_for_category, '') != ''
					  AND sc.gofix_spares_for_category != %(device_category)s
				)
			)"""
			if has_serves_col
			else "1 = 1"
		)
		# Applicability ladder: universal consumable → explicit model fitment →
		# category tier (Laptop Spares only for Laptops) + brand tier (Apple
		# spare never for a Samsung device).
		conditions.append(
			f"""(
				{universal_clause}
				OR EXISTS (
					SELECT 1 FROM `tabGoFix Spare Compatible Model` m2
					WHERE m2.parent = i.name AND m2.parenttype = 'Item'
					  AND m2.device_model = %(device_item)s
				)
				OR (
					NOT EXISTS (
						SELECT 1 FROM `tabGoFix Spare Compatible Model` m
						WHERE m.parent = i.name AND m.parenttype = 'Item'
					)
					AND {category_clause}
					AND (
						IFNULL(i.brand, '') = '' OR %(device_brand)s = ''
						OR LOWER(i.brand) = LOWER(%(device_brand)s)
					)
				)
			)"""
		)

	where = " AND ".join(conditions)
	return frappe.db.sql(
		f"""
		SELECT i.name, i.item_name
		FROM `tabItem` i
		{group_join}
		WHERE {where}
		ORDER BY i.item_name
		LIMIT %(start)s, %(page_len)s
		""",
		values,
	)
