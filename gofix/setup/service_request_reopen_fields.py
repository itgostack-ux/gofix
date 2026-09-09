"""The exception a reopen is approved through.

No new columns: ``tabService Request`` carries 243 columns and 63,460 of the
65,535 bytes MySQL allows in a row, so adding three more varchars to it fails
the ALTER outright — which is exactly what happened when this was first written
that way. The reopen fields the table already has (``reopen_active``,
``reopen_count``, ``reopen_reason``, ``last_reopened_by``, ``last_reopened_at``)
carry the state, and the approval reference goes into ``reopen_reason``, which
is TEXT and therefore stored off-row at no cost to the row limit.
"""

import frappe

REOPEN_EXCEPTION_TYPE = "Service Reopen After QC"
REOPEN_APPROVER_ROLE = "CH Zonal Sales Manager"


def create_service_request_reopen_fields():
	"""Kept as a no-op so an older install's fields are cleaned up.

	The first cut of this added five custom fields and broke the migrate on the
	row-size limit. Removing them here means a bench that ran that version
	recovers on the next migrate instead of failing forever.
	"""
	stale = ("reopen_section", "qc_status_before_reopen", "reopened_at",
	         "reopened_by", "reopen_exception_request")
	for fieldname in stale:
		name = frappe.db.get_value(
			"Custom Field", {"dt": "Service Request", "fieldname": fieldname}, "name")
		if name:
			frappe.delete_doc("Custom Field", name, force=1, ignore_permissions=True)
	frappe.clear_cache(doctype="Service Request")


def create_reopen_exception_type():
	"""The exception a reopen is approved through.

	Routed to the zonal sales manager: reopening is a commercial decision as
	much as a workshop one -- it delays a customer who has been told their
	device is ready, and it is the branch's promise being moved.
	"""
	if not frappe.db.exists("DocType", "CH Exception Type"):
		return
	if frappe.db.exists("CH Exception Type", REOPEN_EXCEPTION_TYPE):
		return

	doc = frappe.new_doc("CH Exception Type")
	doc.update({
		"exception_type": REOPEN_EXCEPTION_TYPE,
		"description": (
			"Putting a repair back on the bench after its quality check has "
			"closed. Allowed only while no invoice exists."
		),
	})
	for field, value in (("is_active", 1), ("enabled", 1),
	                     ("requires_approval", 1), ("approver_role", REOPEN_APPROVER_ROLE)):
		if doc.meta.has_field(field):
			doc.set(field, value)
	doc.insert(ignore_permissions=True)
