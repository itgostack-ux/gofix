# Copyright (c) 2026, GoStack and contributors

"""Fold ``GoFix Token`` into ``POS Kiosk Token``.

The self check-in tablet wrote its own GoFix Token doctype while the counter
logged POS Kiosk Tokens, so one customer became two tokens in two doctypes
that never met. The tablet now writes POS Kiosk Token directly. This patch:

1. copies every GoFix Token (and its selected symptoms) into a POS Kiosk
   Token, mapping the GoFix lifecycle onto the POS one, so no walk-in
   history is lost;
2. drops the GoFix Token, GoFix Token Issue and GoFix Token Status Log
   doctypes and tables, the Desk queue page, and their permission rows.

Idempotent: a token that already has a copy (same store, number and
creation time) is skipped, and everything in step 2 is guarded by existence.
Runs post-model-sync so the new POS Kiosk Token columns exist.
"""

import frappe
from frappe.utils import add_days, get_datetime

# GoFix lifecycle -> POS Kiosk Token lifecycle
STATUS_MAP = {
	"Waiting": "Waiting",
	"Called": "Engaged",
	"Attending": "In Progress",
	"Job Card Created": "Converted",
	"Completed": "Completed",
	"Customer Left": "Dropped",
	"Cancelled": "Cancelled",
}

# GoFix Cancellation Reason -> POS drop_reason Select (else "Other")
DROP_REASON_MAP = {
	"Customer left due to waiting time": "Service Wait Too Long",
	"Price not accepted": "Price Too High",
	"Part not available": "Product Not Available",
	"Service not available": "Product Not Available",
	"Just enquiry": "Just Browsing",
}

DEAD_DOCTYPES = ("GoFix Token Issue", "GoFix Token Status Log", "GoFix Token")


def execute():
	if frappe.db.table_exists("GoFix Token"):
		if not frappe.db.has_column("POS Kiosk Token", "visit_reason"):
			frappe.throw(
				"POS Kiosk Token has not been migrated to its merged schema yet; "
				"pull ch_pos and re-run bench migrate."
			)
		_copy_tokens()
	_drop_dead_objects()


# ---------------------------------------------------------------------------
# 1. data
# ---------------------------------------------------------------------------


def _copy_tokens() -> None:
	rows = frappe.db.sql("SELECT * FROM `tabGoFix Token` ORDER BY creation ASC", as_dict=True)
	if not rows:
		return
	issues: dict[str, list] = {}
	if frappe.db.table_exists("GoFix Token Issue"):
		for row in frappe.db.sql(
			"SELECT parent, symptom_name, device_type, is_expert_check, is_other, symptom_ref "
			"FROM `tabGoFix Token Issue` ORDER BY parent, idx",
			as_dict=True,
		):
			issues.setdefault(row.parent, []).append(row)

	copied = skipped = 0
	for r in rows:
		if frappe.db.exists("POS Kiosk Token", {"store": r.store, "creation": r.creation}):
			skipped += 1
			continue
		_copy_one(r, issues.get(r.name, []))
		copied += 1
	frappe.logger("gofix").info(f"GoFix Token merge: copied {copied}, skipped {skipped} already-migrated")


def _profile_for(row) -> str | None:
	"""POS Profile behind the token's warehouse (CH Store first, then POS Profile)."""
	if frappe.db.table_exists("CH Store"):
		profile = frappe.db.get_value(
			"CH Store", {"warehouse": row.store, "company": row.company, "disabled": 0}, "pos_profile"
		)
		if profile and frappe.db.exists("POS Profile", profile):
			return profile
	return frappe.db.get_value(
		"POS Profile", {"warehouse": row.store, "company": row.company, "disabled": 0}, "name"
	)


def _unique_display(r, profile: str | None) -> str:
	"""Keep the tablet's number unless the counter issued the same one that day.

	Both systems numbered from 001 per store per day, so a tablet GF-X-001 and
	a counter GF-X-001 could coexist. POS Kiosk Token keys (profile, day,
	number) uniquely, so a colliding tablet token takes the next free number
	at that counter for that day.
	"""
	display = r.token_number or r.name
	if not profile:
		return display
	day = str(get_datetime(r.creation).date())
	taken = frappe.db.exists(
		"POS Kiosk Token",
		{
			"pos_profile": profile,
			"token_display": display,
			"creation": ("between", [f"{day} 00:00:00", f"{day} 23:59:59"]),
		},
	)
	if not taken:
		return display
	current_max = frappe.db.sql(
		"""SELECT COALESCE(MAX(CAST(SUBSTRING_INDEX(token_display, '-', -1) AS UNSIGNED)), 0)
		   FROM `tabPOS Kiosk Token` WHERE pos_profile = %s AND DATE(creation) = %s""",
		(profile, day),
	)[0][0]
	prefix = display.rpartition("-")[0] or display
	renumbered = f"{prefix}-{int(current_max) + 1:03d}"
	frappe.logger("gofix").info(
		f"GoFix Token {r.name}: {display} was also issued at the counter on {day}; migrated as {renumbered}"
	)
	return renumbered


def _copy_one(r, issue_rows) -> None:
	status = STATUS_MAP.get(r.status, "Waiting")
	notes = (r.cancellation_notes or "").strip()
	profile = _profile_for(r)
	payload = {
		"doctype": "POS Kiosk Token",
		"pos_profile": profile,
		"company": r.company,
		"store": r.store,
		"status": status,
		"token_display": _unique_display(r, profile),
		"customer_name": r.customer_name,
		"customer_phone": r.customer_phone,
		"customer_language": r.customer_language if r.customer_language in ("English", "Hindi") else "English",
		"visit_source": "Kiosk" if (r.source or "Tablet") == "Tablet" else "Counter",
		"visit_purpose": "Repair" if r.is_repair_visit else "Enquiry",
		"visit_reason": r.visit_reason if frappe.db.exists("GoFix Visit Reason", r.visit_reason or "") else None,
		"device_type": r.device_type or "",
		"device_brand": r.device_brand or "",
		"device_model": r.device_model or "",
		"other_device_hint": r.other_device_hint or "",
		"issue_description": r.additional_notes or "",
		"technician": r.assigned_fde or None,
		"engaged_at": r.called_at,
		"started_at": r.attending_at,
		"completed_at": r.completed_at,
		"exit_at": r.left_at or r.cancelled_at,
		"linked_service_request": r.service_request if r.service_request and frappe.db.exists("Service Request", r.service_request) else None,
		"whatsapp_status": r.whatsapp_status or "Not Sent",
		"whatsapp_sent_at": r.whatsapp_sent_at,
		"whatsapp_message_id": r.whatsapp_message_id,
		"whatsapp_last_error": r.whatsapp_last_error,
		"expires_at": add_days(get_datetime(r.creation), 1),
		"symptoms": [
			{
				"symptom_name": i.symptom_name,
				"device_type": i.device_type,
				"is_expert_check": i.is_expert_check,
				"is_other": i.is_other,
				"symptom_ref": i.symptom_ref if i.symptom_ref and frappe.db.exists("GoFix Symptom", i.symptom_ref) else None,
			}
			for i in issue_rows
		],
	}
	if status in ("Dropped", "Cancelled"):
		reason = r.cancellation_reason or ""
		payload["drop_reason"] = DROP_REASON_MAP.get(reason, "Other")
		payload["drop_sub_reason"] = reason[:140]
		payload["drop_remarks"] = notes
	if r.service_request:
		payload["issue_category"] = frappe.db.get_value("Service Request", r.service_request, "issue_category") or ""

	doc = frappe.get_doc(payload)
	doc.flags.ignore_permissions = True
	doc.flags.ignore_links = True
	doc.flags.ignore_mandatory = True
	doc.creation = r.creation
	doc.owner = r.owner or "Administrator"
	doc.docstatus = 1  # kiosk and counter tokens are submitted on creation
	try:
		doc.insert()
	except frappe.UniqueValidationError:
		raise
	except frappe.ValidationError:
		# Historic rows must land even if today's validators reject old data.
		doc.flags.ignore_validate = True
		doc.insert()
	frappe.db.set_value(
		"POS Kiosk Token",
		doc.name,
		{"creation": r.creation, "modified": r.modified, "modified_by": r.modified_by or "Administrator"},
		update_modified=False,
	)


# ---------------------------------------------------------------------------
# 2. schema + desk objects
# ---------------------------------------------------------------------------


def _drop_dead_objects() -> None:
	for doctype in DEAD_DOCTYPES:
		frappe.db.delete("Custom DocPerm", {"parent": doctype})
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True, ignore_permissions=True)
		frappe.db.sql_ddl(f"DROP TABLE IF EXISTS `tab{doctype}`")

	if frappe.db.exists("Page", "gofix-token-queue"):
		try:
			frappe.delete_doc("Page", "gofix-token-queue", force=True, ignore_missing=True, ignore_permissions=True)
		except Exception:
			frappe.db.delete("Page", {"name": "gofix-token-queue"})

	frappe.db.delete("Workspace Link", {"link_to": ("in", ["GoFix Token", "gofix-token-queue"])})
	frappe.db.delete("Workspace Shortcut", {"link_to": ("in", ["GoFix Token", "gofix-token-queue"])})
	frappe.db.delete("Series", {"name": ("like", "GOFIX-TOKEN::%")})
	if frappe.db.table_exists("CH Role Link"):
		frappe.db.delete(
			"CH Role Link",
			{"parent": "GoFix Settings", "parentfield": "token_transition_override_roles"},
		)
	frappe.clear_cache()
