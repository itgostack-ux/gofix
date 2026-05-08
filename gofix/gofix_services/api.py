# Copyright (c) 2025, GoStack and contributors
# Delivery control, estimate approval, and decision approval APIs for GoFix Service Orders

import frappe
from frappe import _
from frappe.utils import flt, now, today, add_days, random_string, getdate, get_datetime
import secrets


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


# ── Delivery Control ─────────────────────────────────────────────────

@frappe.whitelist()
def generate_delivery_otp(service_order) -> dict:
	"""Generate and send OTP for device handover verification."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

	# Generate 6-digit OTP
	otp = str(secrets.randbelow(900000) + 100000)
	so.db_set("delivery_otp", frappe.utils.password.encrypt(otp), update_modified=False)
	so.db_set("delivery_otp_verified", 0, update_modified=False)
	so.db_set("delivery_otp_sent_at", now(), update_modified=False)

	# Send OTP to customer
	_send_delivery_otp(so, otp)

	return {"message": _("OTP sent to customer"), "otp_sent": True}


@frappe.whitelist()
def verify_delivery_otp(service_order, otp_input) -> dict:
	"""Verify the delivery OTP entered by customer."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

	stored_otp = so.delivery_otp
	if not stored_otp:
		frappe.throw(_("No OTP generated. Please generate OTP first."), title=_("API Error"))

	try:
		decrypted = frappe.utils.password.decrypt(stored_otp)
	except Exception:
		decrypted = stored_otp

	if str(otp_input).strip() != str(decrypted).strip():
		frappe.throw(_("Invalid OTP. Please try again."), title=_("API Error"))

	so.db_set("delivery_otp_verified", 1, update_modified=False)
	return {"message": _("OTP verified successfully"), "verified": True}


@frappe.whitelist()
def validate_delivery_readiness(service_order) -> dict:
	"""Check all delivery gates before allowing device handover."""
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

	blockers = []

	# Gate 1: QC must be passed
	if getattr(so, "qc_status", None) != "Pass":
		blockers.append(_("QC not passed (current: {0})").format(so.qc_status or "Pending"))

	# Gate 2: Payment verified
	if not getattr(so, "payment_verified", None):
		# Check actual invoice status
		has_unpaid = frappe.db.exists("Sales Invoice", {
			"sales_order": service_order,
			"outstanding_amount": [">", 0],
			"docstatus": 1,
		})
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


@frappe.whitelist()
def complete_delivery(service_order, remarks=None) -> dict:
	"""Mark device as delivered after all gates pass."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	so = frappe.get_doc("Sales Order", service_order)

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
		frappe.db.set_value("Service Request", so.service_request, {
			"status": "Delivered",
			"decision": "Delivered",
		}, update_modified=True)

	frappe.msgprint(_("Device delivered successfully"), indicator="green")
	return {"message": "Delivered"}


def _send_delivery_otp(so, otp):
	"""Send delivery OTP via SMS and/or email."""
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


# ── Estimate Approval Flow ───────────────────────────────────────────

@frappe.whitelist()
def send_estimate_to_customer(service_order, send_via="Email") -> dict:
	"""Send repair estimate to customer for approval."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

	so.db_set("estimate_sent", 1, update_modified=False)
	so.db_set("estimate_sent_datetime", now(), update_modified=False)
	so.db_set("estimate_sent_via", send_via, update_modified=False)
	so.db_set("estimate_approval_status", "Pending", update_modified=False)

	# Set expiry (default 3 days)
	so.db_set("estimate_expiry_date", add_days(today(), 3), update_modified=False)

	_send_estimate_notification(so, send_via)

	return {"message": _("Estimate sent to customer via {0}").format(send_via)}


@frappe.whitelist()
def customer_approve_estimate(service_order, remarks=None) -> dict:
	"""Customer approves the repair estimate."""
	so = frappe.get_doc("Sales Order", service_order)
	so.check_permission("write")

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

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

	frappe.msgprint(_("Estimate approved by customer"), indicator="green")
	return {"message": "Estimate approved"}


@frappe.whitelist()
def customer_reject_estimate(service_order, remarks=None) -> dict:
	"""Customer rejects the repair estimate."""
	so = frappe.get_doc("Sales Order", service_order)
	so.check_permission("write")

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

	so.db_set("estimate_approval_status", "Customer Rejected", update_modified=False)
	so.db_set("estimate_approved_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("estimate_customer_remarks", remarks, update_modified=False)

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
			pass


def expire_pending_estimates():
	"""Scheduled task: expire estimates past their expiry date."""
	expired = frappe.db.get_all("Sales Order",
		filters={
			"estimate_approval_status": "Pending",
			"estimate_expiry_date": ["<", today()],
			"is_service_order": 1,
		},
		pluck="name")

	for name in expired:
		frappe.db.set_value("Sales Order", name,
			"estimate_approval_status", "Expired",
			update_modified=False)

	if expired:
		frappe.db.commit()
		frappe.logger("gofix").info(f"Expired {len(expired)} pending estimates")


# ── Decision Approval (maker-checker) ────────────────────────────────

@frappe.whitelist()
def approve_decision(service_order, remarks=None) -> dict:
	"""Manager approves a repair decision that requires approval."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

	so.db_set("decision_approval_status", "Approved", update_modified=False)
	so.db_set("decision_approved_by", frappe.session.user, update_modified=False)
	so.db_set("decision_approval_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("decision_approval_remarks", remarks, update_modified=False)

	frappe.msgprint(_("Decision approved"), indicator="green")
	return {"message": "Decision approved"}


@frappe.whitelist()
def reject_decision(service_order, remarks=None) -> dict:
	"""Manager rejects a repair decision."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

	so.db_set("decision_approval_status", "Rejected", update_modified=False)
	so.db_set("decision_approved_by", frappe.session.user, update_modified=False)
	so.db_set("decision_approval_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("decision_approval_remarks", remarks, update_modified=False)

	frappe.msgprint(_("Decision rejected"), indicator="orange")
	return {"message": "Decision rejected"}


# ── Advance Refund Flow ──────────────────────────────────────────────

@frappe.whitelist()
def process_advance_refund(service_request, amount=None, reason=None) -> dict:
	"""Refund advance payment when device is not repairable.

	Creates a Payment Entry (refund) and updates the Service Request.
	If *amount* is omitted, refunds the full advance_amount.
	"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager"])
	sr = frappe.get_doc("Service Request", service_request)

	advance = flt(sr.advance_amount)
	if not advance:
		frappe.throw(_("No advance payment recorded on this Service Request"), title=_("API Error"))

	refund_amount = flt(amount) or advance
	if refund_amount > advance:
		frappe.throw(_("Refund amount (₹{0}) cannot exceed advance (₹{1})").format(
			refund_amount, advance))

	company = sr.company or frappe.defaults.get_user_default("Company")
	mode_of_payment = sr.advance_received_via or "Cash"

	# Map payment mode to ERPNext Mode of Payment
	mop_map = {"Cash": "Cash", "UPI": "Cash", "Card": "Cash", "Bank Transfer": "Bank Draft"}
	erp_mode = mop_map.get(mode_of_payment, "Cash")

	# Get default accounts
	company_account = frappe.db.get_value("Company", company, "default_cash_account") or \
		frappe.db.get_value("Company", company, "default_bank_account")

	if not company_account:
		frappe.throw(_("Please set default Cash or Bank account for company {0}").format(company), title=_("API Error"))

	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Pay"
	pe.party_type = "Customer"
	pe.party = sr.customer
	pe.company = company
	pe.mode_of_payment = erp_mode
	pe.paid_from = company_account
	pe.paid_to = company_account
	pe.paid_amount = refund_amount
	pe.received_amount = refund_amount
	pe.reference_no = f"Refund-{sr.name}"
	pe.reference_date = today()
	pe.remarks = f"Advance refund for Service Request {sr.name}. Reason: {reason or 'Not Repairable'}"

	pe.insert(ignore_permissions=True)

	# Update Service Request
	sr.db_set("advance_refund_amount", refund_amount, update_modified=False)
	sr.db_set("advance_refund_reason", reason or "Not Repairable", update_modified=False)
	sr.db_set("advance_refund_date", today(), update_modified=False)
	sr.db_set("advance_refund_entry", pe.name, update_modified=False)

	frappe.msgprint(
		_("Advance refund of ₹{0} created: {1}").format(refund_amount, pe.name),
		indicator="green")

	return {"payment_entry": pe.name, "amount": refund_amount}


# ── Inter-Store Service Transfer ─────────────────────────────────────

@frappe.whitelist()
def create_service_transfer(service_request, to_store, reason=None) -> dict:
	"""Transfer a device from source store to a zone service center for repair.

	Updates Service Request transfer tracking fields. The device physically
	moves via a Stock Entry (Material Transfer) created separately.
	"""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	sr = frappe.get_doc("Service Request", service_request)

	if sr.transfer_status in ("In Transit", "Received at Service Center"):
		frappe.throw(_("Device is already in transfer (status: {0})").format(sr.transfer_status), title=_("API Error"))

	if not to_store:
		frappe.throw(_("Destination store/service center is required"), title=_("API Error"))

	sr.db_set("transferred_to_store", to_store, update_modified=True)
	sr.db_set("transfer_status", "In Transit", update_modified=False)
	sr.db_set("transfer_date", today(), update_modified=False)
	sr.db_set("transfer_reason", reason or "", update_modified=False)

	# Update current location
	sr.db_set("current_location", None, update_modified=False)  # In transit

	# Also update SO current_location if exists
	if sr.service_order:
		frappe.db.set_value("Sales Order", sr.service_order,
			"current_location", None, update_modified=False)

	frappe.msgprint(
		_("Device transfer initiated: {0} → {1}").format(sr.source_warehouse, to_store),
		indicator="blue")

	return {"status": "In Transit", "from": sr.source_warehouse, "to": to_store}


@frappe.whitelist()
def receive_service_transfer(service_request) -> dict:
	"""Mark device as received at the destination service center."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	sr = frappe.get_doc("Service Request", service_request)

	if sr.transfer_status != "In Transit":
		frappe.throw(_("Device is not in transit (status: {0})").format(sr.transfer_status), title=_("API Error"))

	sr.db_set("transfer_status", "Received at Service Center", update_modified=True)
	sr.db_set("transfer_received_date", today(), update_modified=False)
	sr.db_set("current_location", sr.transferred_to_store, update_modified=False)

	if sr.service_order:
		frappe.db.set_value("Sales Order", sr.service_order,
			"current_location", sr.transferred_to_store, update_modified=False)

	frappe.msgprint(_("Device received at service center"), indicator="green")
	return {"status": "Received at Service Center"}


@frappe.whitelist()
def return_service_transfer(service_request) -> dict:
	"""Initiate return of device from service center back to origin store."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	sr = frappe.get_doc("Service Request", service_request)

	if sr.transfer_status not in ("Received at Service Center", "Repair Complete"):
		frappe.throw(_("Device must be at service center to initiate return (status: {0})").format(
			sr.transfer_status))

	sr.db_set("transfer_status", "Return In Transit", update_modified=True)
	sr.db_set("current_location", None, update_modified=False)

	frappe.msgprint(_("Return transfer initiated"), indicator="blue")
	return {"status": "Return In Transit"}


@frappe.whitelist()
def complete_service_transfer_return(service_request) -> dict:
	"""Mark device as returned to the origin store."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	sr = frappe.get_doc("Service Request", service_request)

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

@frappe.whitelist()
def schedule_pickup(service_request, address, scheduled_datetime, agent=None) -> dict:
	"""Schedule a device pickup for outstation/courier mode service requests."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	sr = frappe.get_doc("Service Request", service_request)

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


@frappe.whitelist()
def complete_pickup(service_request) -> dict:
	"""Mark device pickup as completed."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	sr = frappe.get_doc("Service Request", service_request)

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


@frappe.whitelist()
def dispatch_return(service_request, courier_name=None, tracking_number=None) -> dict:
	"""Dispatch repaired device back to customer via courier."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	sr = frappe.get_doc("Service Request", service_request)

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


@frappe.whitelist()
def confirm_return_delivery(service_request) -> dict:
	"""Confirm customer received the returned device."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	sr = frappe.get_doc("Service Request", service_request)

	if sr.get("mode_of_service") == "Courier" and not sr.get("return_dispatched_date"):
		frappe.throw(_("Return must be dispatched before delivery can be confirmed."), title=_("Dispatch Pending"))

	sr.db_set("return_delivered_date", today(), update_modified=True)
	sr.db_set("status", "Delivered", update_modified=False)
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
	so = frappe.get_doc("Sales Order", service_order)
	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"), title=_("API Error"))

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
		fields=["actual_hours", "service_engineer", "service_engineer as engineer_name"])

	labor_details = []
	suggested_labor = 0
	for js in job_sheets:
		hours = flt(js.actual_hours)
		hourly_rate = 0
		engineer_name = js.engineer_name or "Unassigned"
		if js.service_engineer:
			hourly_rate = flt(frappe.db.get_value("Employee", js.service_engineer, "custom_hourly_rate"))
			if not hourly_rate:
				ctc = flt(frappe.db.get_value("Employee", js.service_engineer, "ctc"))
				if ctc:
					hourly_rate = ctc / 2080
			engineer_name = frappe.db.get_value("Employee", js.service_engineer, "employee_name") or engineer_name

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


# ── Issue → Solution → Spare Cascade APIs ────────────────────────────

@frappe.whitelist()
def get_solutions_for_issues(issue_categories) -> list:
	"""Return active Repair Solutions for the given issue categories.
	Args:
		issue_categories: JSON list of Issue Category names
	"""
	import json
	if isinstance(issue_categories, str):
		issue_categories = json.loads(issue_categories)

	if not issue_categories:
		return []

	return frappe.get_all("Repair Solution",
		filters={
			"issue_category": ["in", issue_categories],
			"is_active": 1
		},
		fields=["name", "solution_name", "issue_category", "solution_code",
				"estimated_minutes", "requires_spare", "skill_level", "minimum_grade"],
		order_by="issue_category, solution_name"
	)


@frappe.whitelist()
def get_spares_for_solution(repair_solution) -> list:
	"""Return active mapped spares for a given Repair Solution."""
	if not repair_solution:
		return []

	return frappe.get_all("Solution Spare Mapping",
		filters={
			"repair_solution": repair_solution,
			"is_active": 1
		},
		fields=["name", "spare_item", "item_name", "default_qty", "uom", "is_mandatory"],
		order_by="is_mandatory desc, item_name"
	)


@frappe.whitelist()
def get_spares_for_solutions(repair_solutions) -> list:
	"""Return active mapped spares for multiple Repair Solutions.
	Args:
		repair_solutions: JSON list of Repair Solution names
	"""
	import json
	if isinstance(repair_solutions, str):
		repair_solutions = json.loads(repair_solutions)

	if not repair_solutions:
		return []

	return frappe.get_all("Solution Spare Mapping",
		filters={
			"repair_solution": ["in", repair_solutions],
			"is_active": 1
		},
		fields=["name", "repair_solution", "spare_item", "item_name",
				"default_qty", "uom", "is_mandatory"],
		order_by="repair_solution, is_mandatory desc, item_name"
	)


@frappe.whitelist()
def get_eligible_technicians(issue_categories, warehouse=None) -> list:
	"""Return technicians (Employees) whose Technician Grade covers all given issues.
	Args:
		issue_categories: JSON list of Issue Category names
		warehouse: optional warehouse to filter by default_shift location
	"""
	import json
	if isinstance(issue_categories, str):
		issue_categories = json.loads(issue_categories)

	if not issue_categories:
		return []

	# Get minimum skill requirements from Repair Solution masters
	required_skills = {}
	solutions = frappe.get_all("Repair Solution",
		filters={"issue_category": ["in", issue_categories], "is_active": 1},
		fields=["issue_category", "skill_level"],
		group_by="issue_category"
	)
	skill_order = {"Basic": 1, "Intermediate": 2, "Advanced": 3, "Expert": 4}
	for s in solutions:
		cat = s.issue_category
		level = skill_order.get(s.skill_level, 1)
		if cat not in required_skills or level > required_skills[cat]:
			required_skills[cat] = level

	# Find all grades whose skills cover the required categories at the right level
	grades = frappe.get_all("Technician Grade", filters={"is_active": 1}, fields=["name", "grade_level"])
	eligible_grades = []
	for grade in grades:
		skills = frappe.get_all("Technician Skill",
			filters={"parent": grade.name},
			fields=["issue_category", "max_skill_level"]
		)
		skill_map = {s.issue_category: skill_order.get(s.max_skill_level, 1) for s in skills}
		covers_all = True
		for cat, req_level in required_skills.items():
			if cat not in skill_map or skill_map[cat] < req_level:
				covers_all = False
				break
		if covers_all:
			eligible_grades.append(grade.name)

	if not eligible_grades:
		return []

	# Find employees with these grades
	filters = {"technician_grade": ["in", eligible_grades], "status": "Active"}
	if warehouse:
		filters["default_shift"] = warehouse  # or use a custom field for store assignment

	return frappe.get_all("Employee",
		filters=filters,
		fields=["name", "employee_name", "technician_grade", "designation"],
		order_by="employee_name"
	)


@frappe.whitelist()
def get_mapped_spare_items(doctype, txt, searchfield, start, page_len, filters) -> list:
	"""Server-side query for Link field: returns Items mapped to a Repair Solution.
	Used as 'query' in spare_item get_query on SR Spare Line.
	"""
	repair_solution = filters.get("repair_solution")
	if not repair_solution:
		return []

	mapped = frappe.get_all("Solution Spare Mapping",
		filters={"repair_solution": repair_solution, "is_active": 1},
		pluck="spare_item"
	)
	if not mapped:
		return []

	return frappe.get_all("Item",
		filters=[
			["name", "in", mapped],
			["name", "like", f"%{txt}%"] if txt else ["name", "in", mapped]
		],
		fields=["name", "item_name"],
		as_list=True,
		limit_page_length=page_len,
		limit_start=start
	)
