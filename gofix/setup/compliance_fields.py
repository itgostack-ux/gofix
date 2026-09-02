# Copyright (c) 2026, GoFix and contributors

"""Intake controls a repair operator has to be able to evidence.

Three things happen at a counter that nothing downstream can undo:

* the handset is accepted (and if it was reported stolen, that is now a police
  matter rather than a service one),
* somebody takes custody of a stranger's personal data,
* the customer walks away, and from then on the device's condition at drop-off
  is one person's word against another's.

None of these were recorded. This installs the fields that record them, and
``gofix.compliance`` holds the gates that make them mean something.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Mirrors Buyback Order's vocabulary deliberately. Both modules ask staff to run
# the same lookup on the same portal, and one shared set of words means a
# blacklisted handset reads identically whichever door it came through.
IMEI_STATUSES = "Pending\nVerified Clean\nBlacklisted\nDuplicate IMEI\nAlready In Use\nCould Not Verify"
IMEI_BLOCKING = ("Blacklisted", "Duplicate IMEI", "Already In Use")

LOCK_STATUSES = "Not Checked\nNo Lock\nLocked — Credentials Provided\nLocked — Customer Cannot Unlock"

WIPE_METHODS = "Not Wiped\nFactory Reset\nSecure Erase\nCustomer Declined Wipe\nNot Applicable — No Data Access"


def create_compliance_fields():
	_service_request_intake_controls()
	_customer_device_custody_fields()
	_reopen_traceability_fields()
	_close_outcome_field()


def _close_outcome_field():
	"""Why a job ended without a working device, in one countable field.

	decision says Rejected or Cancelled and repairability_status says the
	technical verdict, but neither separates "we could not fix it" from "it was
	not worth fixing" from "the customer said no". That separation is the whole
	point of measuring failed jobs, so it gets its own field rather than being
	inferred from two others.
	"""
	if not frappe.db.exists("DocType", "Service Request"):
		return

	create_custom_fields({
		"Service Request": [
			{
				"fieldname": "close_outcome",
				"label": "Closed Without Repair As",
				"fieldtype": "Select",
				"options": "\nNot Repairable\nBER\nCustomer Declined\nCustomer Cancelled",
				"insert_after": "repairability_reason",
				"read_only": 1,
				"allow_on_submit": 1,
				"in_standard_filter": 1,
				"description": "Set by the closing action; the coded reason is on Withdrawal Reason.",
			},
		]
	}, ignore_validate=True)


def _reopen_traceability_fields():
	"""Which repair, and which part of it, the customer came back about.

	Repeat detection already links the previous ticket, but a ticket can carry
	several solutions and only one of them usually failed. Naming the specific
	line is what turns "this device came back" into "the screen we replaced
	came back" — which is the difference between a statistic and a fault you
	can act on, and it is what a supplier claim or a technician review needs.
	"""
	if not frappe.db.exists("DocType", "SR Issue Line"):
		return

	create_custom_fields({
		"SR Issue Line": [
			{
				"fieldname": "reopened_from_solution",
				"label": "Came Back On",
				"fieldtype": "Link",
				"options": "SR Solution Line",
				"insert_after": "status",
				"read_only": 1,
				"description": "The earlier repair line this complaint is a return visit for.",
			},
			{
				"fieldname": "reopened_from_request",
				"label": "Original Repair",
				"fieldtype": "Link",
				"options": "Service Request",
				"insert_after": "reopened_from_solution",
				"read_only": 1,
			},
		]
	}, ignore_validate=True)


def _customer_device_custody_fields():
	"""Where the customer's device is being held, and under which postings.

	A handset in for repair is customer special stock: received at zero
	valuation so it can be tracked, moved and scanned without ever touching the
	balance sheet. These fields are the ticket's link to those postings — see
	gofix.customer_device_stock.
	"""
	if not frappe.db.exists("DocType", "Service Request"):
		return

	create_custom_fields({
		"Service Request": [
			{
				"fieldname": "customer_device_section",
				"label": "Device Custody",
				"fieldtype": "Section Break",
				"insert_after": "intake_condition_photos",
				"collapsible": 1,
			},
			{
				"fieldname": "customer_device_warehouse",
				"label": "Held In",
				"fieldtype": "Link",
				"options": "Warehouse",
				"insert_after": "customer_device_section",
				"read_only": 1,
				"allow_on_submit": 1,
				# Blank until custody is taken. Under strict user permissions a
				# blank Warehouse link DENIES the whole document to any user who
				# carries Warehouse User Permissions -- i.e. every scoped store
				# user -- which blocked Service Request creation outright. The
				# custody trail is enforced by its own logic, not by this field.
				"ignore_user_permissions": 1,
				"description": "The Customer Device bin currently holding this handset.",
			},
			{
				"fieldname": "customer_device_entry",
				"label": "Custody Receipt",
				"fieldtype": "Link",
				"options": "Stock Entry",
				"insert_after": "customer_device_warehouse",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "column_break_customer_device",
				"fieldtype": "Column Break",
				"insert_after": "customer_device_entry",
			},
			{
				"fieldname": "customer_device_released_entry",
				"label": "Handback Issue",
				"fieldtype": "Link",
				"options": "Stock Entry",
				"insert_after": "column_break_customer_device",
				"read_only": 1,
				"allow_on_submit": 1,
			},
		]
	}, ignore_validate=True)

	if frappe.db.exists("DocType", "GoFix Settings"):
		create_custom_fields({
			"GoFix Settings": [
				{
					"fieldname": "track_customer_devices",
					"label": "Track Customer Devices as Special Stock",
					"fieldtype": "Check",
					"default": "0",
					"insert_after": "capacity_warning_ratio",
					"description": (
						"Receive a customer's handset into a zero-valuation Customer Device "
						"bin at intake, so it can be moved on a manifest, scanned by a driver "
						"and counted. Valuation stays nil, so no financial statement moves. "
						"Off by default: switching it on mid-flight only affects tickets "
						"opened afterwards."
					),
				},
			]
		}, ignore_validate=True)


def _service_request_intake_controls():
	if not frappe.db.exists("DocType", "Service Request"):
		return

	create_custom_fields({
		"Service Request": [
			# ── Stolen-handset screening ───────────────────────────────
			{
				"fieldname": "imei_screening_section",
				"label": "IMEI Screening (CEIR / Sanchar Saathi)",
				"fieldtype": "Section Break",
				"insert_after": "actual_imei",
				"collapsible": 1,
			},
			{
				"fieldname": "imei_validation_status",
				"label": "IMEI Screening Result",
				"fieldtype": "Select",
				"options": IMEI_STATUSES,
				"default": "Pending",
				"insert_after": "imei_screening_section",
				"allow_on_submit": 1,
				"in_standard_filter": 1,
				"description": (
					"Staff look the IMEI up on ceir.sancharsaathi.gov.in — dial *#06# for the "
					"IMEI, or SMS \"KYM &lt;imei&gt;\" to 14422. There is no public API to call."
				),
			},
			{
				"fieldname": "imei_validation_checked_by",
				"label": "Screened By",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "imei_validation_status",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "column_break_imei_screening",
				"fieldtype": "Column Break",
				"insert_after": "imei_validation_checked_by",
			},
			{
				"fieldname": "imei_validation_checked_at",
				"label": "Screened At",
				"fieldtype": "Datetime",
				"insert_after": "column_break_imei_screening",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "imei_validation_screenshot",
				"label": "Portal Screenshot",
				"fieldtype": "Attach Image",
				"insert_after": "imei_validation_checked_at",
				"allow_on_submit": 1,
				"description": "Proof of the lookup, for when the result is questioned later.",
			},
			{
				"fieldname": "imei_validation_remarks",
				"label": "Screening Remarks",
				"fieldtype": "Small Text",
				"insert_after": "imei_validation_screenshot",
				"allow_on_submit": 1,
			},

			# ── Activation lock ────────────────────────────────────────
			{
				"fieldname": "activation_lock_status",
				"label": "Activation Lock (iCloud / FRP)",
				"fieldtype": "Select",
				"options": LOCK_STATUSES,
				"default": "Not Checked",
				"insert_after": "imei_validation_remarks",
				"allow_on_submit": 1,
				"description": (
					"A locked device can be repaired perfectly and still fail every functional "
					"test, because it never boots past the lock."
				),
			},

			# ── Data handling ──────────────────────────────────────────
			{
				"fieldname": "data_handling_section",
				"label": "Customer Data Handling",
				"fieldtype": "Section Break",
				"insert_after": "backup_info",
				"collapsible": 1,
			},
			{
				"fieldname": "data_access_required",
				"label": "Repair Requires Access to Customer Data",
				"fieldtype": "Check",
				"default": "0",
				"insert_after": "data_handling_section",
				"allow_on_submit": 1,
				"description": "Tick when the technician must unlock the device or read its contents.",
			},
			{
				"fieldname": "data_access_consent",
				"label": "Customer Consented to Data Access",
				"fieldtype": "Check",
				"default": "0",
				"insert_after": "data_access_required",
				"allow_on_submit": 1,
				"depends_on": "eval:doc.data_access_required",
			},
			{
				"fieldname": "data_wipe_method",
				"label": "Data Wipe Performed",
				"fieldtype": "Select",
				"options": WIPE_METHODS,
				"default": "Not Wiped",
				"insert_after": "data_access_consent",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "column_break_data_handling",
				"fieldtype": "Column Break",
				"insert_after": "data_wipe_method",
			},
			{
				"fieldname": "data_wipe_by",
				"label": "Wiped By",
				"fieldtype": "Link",
				"options": "User",
				"insert_after": "column_break_data_handling",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "data_wipe_at",
				"label": "Wiped At",
				"fieldtype": "Datetime",
				"insert_after": "data_wipe_by",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "data_wipe_remarks",
				"label": "Wipe Remarks",
				"fieldtype": "Small Text",
				"insert_after": "data_wipe_at",
				"allow_on_submit": 1,
			},

			# ── Intake acknowledgement ─────────────────────────────────
			{
				"fieldname": "intake_ack_section",
				"label": "Intake Acknowledgement",
				"fieldtype": "Section Break",
				"insert_after": "product_condition_desc",
				"collapsible": 1,
			},
			{
				"fieldname": "intake_signature",
				"label": "Customer Signature at Drop-off",
				"fieldtype": "Signature",
				"insert_after": "intake_ack_section",
				"allow_on_submit": 1,
				"description": (
					"Signed acknowledgement of the device's condition, the accessories handed "
					"over, and the risk to data. This is the signature that settles a handback "
					"dispute — the one at collection cannot."
				),
			},
			{
				"fieldname": "intake_signed_at",
				"label": "Signed At",
				"fieldtype": "Datetime",
				"insert_after": "intake_signature",
				"read_only": 1,
				"allow_on_submit": 1,
			},
			{
				"fieldname": "intake_condition_photos",
				"label": "Condition Photos at Drop-off",
				"fieldtype": "Attach",
				"insert_after": "intake_signed_at",
				"allow_on_submit": 1,
				"description": "Photographic record of the device as received.",
			},
		]
	}, ignore_validate=True)

	frappe.logger("gofix").info("GoFix: intake compliance fields installed")
