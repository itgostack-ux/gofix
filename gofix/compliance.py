# Copyright (c) 2026, GoFix and contributors

"""The checks that have to hold before a repair goes any further.

A field that records a check is not a control. What makes it one is something
refusing to continue while the answer is wrong, so each gate here is called from
the point in the flow where turning back is still cheap:

* **IMEI screening** blocks at intake. Repairing a handset that is reported
  stolen nationally is not a service problem to be discovered later.
* **Activation lock** blocks at assignment, not intake — the device is already
  on the counter, and the question is whether to spend bench time on it.
* **Data-access consent** blocks at repair start, because that is when somebody
  actually unlocks the phone.
* **Wipe evidence** blocks at handback, which is the last moment the device is
  still in the building.

Every gate is skipped when its field has not been installed yet, so a site that
migrates code before data keeps working.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from gofix.setup.compliance_fields import IMEI_BLOCKING

# Screening outcomes that permit work to start.
IMEI_CLEAN = "Verified Clean"

# Lock states that mean the technician can actually boot the device.
LOCK_WORKABLE = ("No Lock", "Locked — Credentials Provided")

WIPE_NOT_DONE = "Not Wiped"


def _has(fieldname: str) -> bool:
	return bool(frappe.get_meta("Service Request").get_field(fieldname))


# ── stolen-handset screening ─────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def submit_imei_screening(service_request, status, screenshot=None, remarks=None) -> dict:
	"""Record the result of the manual CEIR / Sanchar Saathi lookup.

	There is no public API for the national registry, so staff run the lookup
	themselves on ceir.sancharsaathi.gov.in and report what they saw, with a
	screenshot as proof. This mirrors how Buyback Order handles the identical
	question, down to the wording of the outcomes.

	Only "Verified Clean" lets the repair proceed. The three bad outcomes are
	recorded and the ticket stops. "Could Not Verify" — the portal was down —
	also stops it, but without prejudice: re-run the lookup and submit again.
	"""
	sr = frappe.get_doc("Service Request", service_request)
	sr.check_permission("write")

	allowed = {IMEI_CLEAN, "Could Not Verify", *IMEI_BLOCKING}
	status = (status or "").strip()
	if status not in allowed:
		frappe.throw(
			_("Unknown screening result {0}. Expected one of: {1}").format(
				status, ", ".join(sorted(allowed))
			),
			title=_("Invalid Screening Result"),
		)

	updates = {
		"imei_validation_status": status,
		"imei_validation_checked_by": frappe.session.user,
		"imei_validation_checked_at": now_datetime(),
	}
	if screenshot:
		updates["imei_validation_screenshot"] = screenshot
	if remarks:
		updates["imei_validation_remarks"] = remarks
	sr.db_set({k: v for k, v in updates.items() if _has(k)}, update_modified=True)

	sr.add_comment(
		"Info",
		_("CEIR screening recorded as <b>{0}</b> by {1}.").format(status, frappe.session.user),
	)

	if status in IMEI_BLOCKING:
		frappe.msgprint(
			_("This handset came back <b>{0}</b> on the national registry. The repair cannot "
			  "proceed. Escalate to the store manager before returning the device.").format(status),
			title=_("Device Flagged"),
			indicator="red",
		)

	return {"ok": True, "status": status, "clean": status == IMEI_CLEAN}


def assert_imei_screened(sr, action: str) -> None:
	"""Refuse to start work on an unscreened or flagged handset."""
	if not _has("imei_validation_status"):
		return

	status = (sr.get("imei_validation_status") or "Pending").strip()
	if status == IMEI_CLEAN:
		return

	if status in IMEI_BLOCKING:
		frappe.throw(
			_("Cannot {0}: this handset is <b>{1}</b> on the national registry (CEIR). "
			  "A device reported lost or stolen must not be repaired.").format(action, status),
			title=_("Device Flagged"),
		)

	frappe.throw(
		_("Cannot {0}: the IMEI has not been screened yet (current result: {1}). "
		  "Look the IMEI up on ceir.sancharsaathi.gov.in and record the result "
		  "before work starts.").format(action, status or _("Pending")),
		title=_("IMEI Screening Required"),
	)


# ── activation lock ──────────────────────────────────────────────────────────

def assert_device_bootable(sr, action: str) -> None:
	"""Warn or block when the device is locked out of the technician's reach.

	Unchecked is a warning — plenty of repairs never need the device to boot.
	A device the customer cannot unlock is a hard stop: the work can be done
	perfectly and still fail QC, because nothing can be tested.
	"""
	if not _has("activation_lock_status"):
		return

	status = (sr.get("activation_lock_status") or "Not Checked").strip()
	if status in LOCK_WORKABLE:
		return

	if status == "Locked — Customer Cannot Unlock":
		frappe.throw(
			_("Cannot {0}: the device is locked (iCloud / FRP) and the customer cannot unlock "
			  "it. Nothing can be functionally tested, so the repair cannot be signed off. "
			  "Resolve the lock with the customer, or quote it as an unlock job.").format(action),
			title=_("Device Locked"),
		)

	frappe.msgprint(
		_("Activation lock has not been checked on this device. If it is locked, the repair "
		  "cannot be functionally tested at QC."),
		title=_("Activation Lock Not Checked"),
		indicator="orange",
	)


# ── customer data ────────────────────────────────────────────────────────────

def assert_data_access_consented(sr, action: str) -> None:
	"""No unlocking a customer's phone without their say-so.

	Only bites when the ticket says the repair needs it. Holding somebody's
	device makes you a data processor; a disclaimer that data "may be lost" is
	not consent to go looking through it.
	"""
	if not (_has("data_access_required") and _has("data_access_consent")):
		return
	if not cint(sr.get("data_access_required")):
		return
	if cint(sr.get("data_access_consent")):
		return

	frappe.throw(
		_("Cannot {0}: this repair needs access to the customer's data and they have not "
		  "consented. Record their consent on the ticket first.").format(action),
		title=_("Data Access Consent Required"),
	)


@frappe.whitelist(methods=["POST"])
def record_data_wipe(service_request, method, remarks=None) -> dict:
	"""Record what was done to the customer's data, and by whom."""
	sr = frappe.get_doc("Service Request", service_request)
	sr.check_permission("write")

	valid = set(
		(frappe.get_meta("Service Request").get_field("data_wipe_method").options or "").split("\n")
	) - {""}
	method = (method or "").strip()
	if valid and method not in valid:
		frappe.throw(
			_("Unknown wipe method {0}. Expected one of: {1}").format(method, ", ".join(sorted(valid))),
			title=_("Invalid Wipe Method"),
		)

	sr.db_set({
		"data_wipe_method": method,
		"data_wipe_by": frappe.session.user,
		"data_wipe_at": now_datetime(),
		"data_wipe_remarks": remarks or "",
	}, update_modified=True)
	sr.add_comment("Info", _("Data handling recorded: <b>{0}</b>.").format(method))
	return {"ok": True, "method": method}


def assert_data_handling_recorded(sr, action: str) -> None:
	"""Before the device goes home, say what happened to the data on it.

	"Not Applicable — No Data Access" and "Customer Declined Wipe" are perfectly
	good answers. Silence is not: the point is that a decision was made and
	attributed, not that every device gets erased.
	"""
	if not _has("data_wipe_method"):
		return
	if not cint(sr.get("data_access_required")):
		return

	method = (sr.get("data_wipe_method") or WIPE_NOT_DONE).strip()
	if method and method != WIPE_NOT_DONE:
		return

	frappe.throw(
		_("Cannot {0}: this repair accessed customer data and nothing records what happened "
		  "to it. Record the outcome — a wipe, a customer refusal, or not applicable — "
		  "before the device leaves.").format(action),
		title=_("Data Handling Record Required"),
	)


# ── intake acknowledgement ───────────────────────────────────────────────────

def assert_intake_acknowledged(sr, action: str) -> None:
	"""The customer's signature on the condition the device arrived in.

	Handback arguments are almost never about the repair; they are about a mark
	on the case that one side says was already there. Only the drop-off
	signature answers that, so it is captured before the job opens rather than
	alongside the collection signature, which comes far too late to help.
	"""
	if not _has("intake_signature"):
		return
	if (sr.get("intake_signature") or "").strip():
		return

	frappe.throw(
		_("Cannot {0}: the customer has not signed for the device's condition at drop-off. "
		  "Capture the intake signature on the ticket first.").format(action),
		title=_("Intake Signature Required"),
	)


@frappe.whitelist(methods=["POST"])
def record_intake_signature(service_request, signature, condition_photos=None) -> dict:
	"""Store the drop-off signature and stamp when it was given."""
	sr = frappe.get_doc("Service Request", service_request)
	sr.check_permission("write")

	if not (signature or "").strip():
		frappe.throw(_("No signature was captured."), title=_("Validation Error"))

	updates = {"intake_signature": signature, "intake_signed_at": now_datetime()}
	if condition_photos:
		updates["intake_condition_photos"] = condition_photos
	sr.db_set({k: v for k, v in updates.items() if _has(k)}, update_modified=True)
	sr.add_comment("Info", _("Customer signed for the device's condition at drop-off."))
	return {"ok": True}


# ── the combined gates the flow calls ────────────────────────────────────────

def assert_safe_to_start_work(sr, action: str) -> None:
	"""Everything that must hold before bench time is spent on a device."""
	assert_imei_screened(sr, action)
	assert_data_access_consented(sr, action)
	assert_device_bootable(sr, action)


def assert_safe_to_hand_back(sr, action: str) -> None:
	"""Everything that must hold before the device leaves the building."""
	assert_data_handling_recorded(sr, action)
