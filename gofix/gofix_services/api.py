# Copyright (c) 2025, GoStack and contributors
# Delivery control, estimate approval, and decision approval APIs for GoFix Service Orders

import frappe
from frappe import _
from frappe.utils import flt, now, today, add_days, random_string, getdate
import secrets


# ── Delivery Control ─────────────────────────────────────────────────

@frappe.whitelist()
def generate_delivery_otp(service_order):
	"""Generate and send OTP for device handover verification."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

	# Generate 6-digit OTP
	otp = str(secrets.randbelow(900000) + 100000)
	so.db_set("delivery_otp", frappe.utils.password.encrypt(otp), update_modified=False)
	so.db_set("delivery_otp_verified", 0, update_modified=False)
	so.db_set("delivery_otp_sent_at", now(), update_modified=False)

	# Send OTP to customer
	_send_delivery_otp(so, otp)

	return {"message": _("OTP sent to customer"), "otp_sent": True}


@frappe.whitelist()
def verify_delivery_otp(service_order, otp_input):
	"""Verify the delivery OTP entered by customer."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

	stored_otp = so.delivery_otp
	if not stored_otp:
		frappe.throw(_("No OTP generated. Please generate OTP first."))

	try:
		decrypted = frappe.utils.password.decrypt(stored_otp)
	except Exception:
		decrypted = stored_otp

	if str(otp_input).strip() != str(decrypted).strip():
		frappe.throw(_("Invalid OTP. Please try again."))

	so.db_set("delivery_otp_verified", 1, update_modified=False)
	return {"message": _("OTP verified successfully"), "verified": True}


@frappe.whitelist()
def validate_delivery_readiness(service_order):
	"""Check all delivery gates before allowing device handover."""
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

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
def complete_delivery(service_order, remarks=None):
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
			frappe.sendmail(
				recipients=[customer_email],
				subject=f"GoFix Device Collection OTP — {so.name}",
				message=(
					f"Dear {customer_name},<br><br>"
					f"Your OTP for device collection is: <b>{otp}</b><br><br>"
					f"Please share this with the store executive when collecting your device.<br>"
					f"Service Order: {so.name}<br><br>"
					f"Thank you for choosing GoFix."
				),
				reference_doctype="Sales Order",
				reference_name=so.name,
				now=True,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Delivery OTP email failed")


# ── Estimate Approval Flow ───────────────────────────────────────────

@frappe.whitelist()
def send_estimate_to_customer(service_order, send_via="Email"):
	"""Send repair estimate to customer for approval."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Sales User"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

	so.db_set("estimate_sent", 1, update_modified=False)
	so.db_set("estimate_sent_datetime", now(), update_modified=False)
	so.db_set("estimate_sent_via", send_via, update_modified=False)
	so.db_set("estimate_approval_status", "Pending", update_modified=False)

	# Set expiry (default 3 days)
	so.db_set("estimate_expiry_date", add_days(today(), 3), update_modified=False)

	_send_estimate_notification(so, send_via)

	return {"message": _("Estimate sent to customer via {0}").format(send_via)}


@frappe.whitelist()
def customer_approve_estimate(service_order, remarks=None):
	"""Customer approves the repair estimate."""
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

	if getattr(so, "estimate_approval_status", None) not in ("Pending", None, ""):
		frappe.throw(_("Estimate is not pending approval (current: {0})").format(
			so.estimate_approval_status))

	# Check expiry
	if so.estimate_expiry_date and getdate(so.estimate_expiry_date) < getdate(today()):
		so.db_set("estimate_approval_status", "Expired", update_modified=False)
		frappe.throw(_("This estimate has expired. Please request a new estimate."))

	so.db_set("estimate_approval_status", "Customer Approved", update_modified=False)
	so.db_set("estimate_approved_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("estimate_customer_remarks", remarks, update_modified=False)

	frappe.msgprint(_("Estimate approved by customer"), indicator="green")
	return {"message": "Estimate approved"}


@frappe.whitelist()
def customer_reject_estimate(service_order, remarks=None):
	"""Customer rejects the repair estimate."""
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

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
			frappe.sendmail(
				recipients=[customer_email],
				subject=f"GoFix Repair Estimate — {so.name}",
				message=(
					f"Dear {customer_name},<br><br>"
					f"Your repair estimate is ready:<br><br>"
					f"<b>Device:</b> {getattr(so, 'device_model', '') or 'N/A'}<br>"
					f"<b>Issue:</b> {getattr(so, 'issue_description', '') or 'N/A'}<br>"
					f"<b>Estimated Cost:</b> ₹{total:,.2f}<br>"
					f"<b>Valid Until:</b> {so.estimate_expiry_date or 'N/A'}<br><br>"
					f"Please reply to approve or reject this estimate.<br><br>"
					f"Thank you for choosing GoFix."
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
def approve_decision(service_order, remarks=None):
	"""Manager approves a repair decision that requires approval."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

	so.db_set("decision_approval_status", "Approved", update_modified=False)
	so.db_set("decision_approved_by", frappe.session.user, update_modified=False)
	so.db_set("decision_approval_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("decision_approval_remarks", remarks, update_modified=False)

	frappe.msgprint(_("Decision approved"), indicator="green")
	return {"message": "Decision approved"}


@frappe.whitelist()
def reject_decision(service_order, remarks=None):
	"""Manager rejects a repair decision."""
	frappe.only_for(["Sales Manager", "System Manager", "Service Manager", "Store Manager"])
	so = frappe.get_doc("Sales Order", service_order)

	if not so.is_service_order:
		frappe.throw(_("Not a Service Order"))

	so.db_set("decision_approval_status", "Rejected", update_modified=False)
	so.db_set("decision_approved_by", frappe.session.user, update_modified=False)
	so.db_set("decision_approval_datetime", now(), update_modified=False)
	if remarks:
		so.db_set("decision_approval_remarks", remarks, update_modified=False)

	frappe.msgprint(_("Decision rejected"), indicator="orange")
	return {"message": "Decision rejected"}
