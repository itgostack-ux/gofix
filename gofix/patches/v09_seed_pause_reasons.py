# Copyright (c) 2026, GoFix and contributors

"""Seed the pause reasons a workshop actually uses.

A repair sitting "On Hold" with no reason is the most expensive kind of silence:
nobody can tell whether it is waiting on a technician, a part, or the customer,
and those three go to three different people to unblock. These are the coded
reasons the floor picks from; a branch may add its own — the list is a master,
not an enum.

``reason_type`` is what makes the pause time readable in reporting: technician
time is workshop capacity, waiting on parts is procurement, waiting on the
customer is not the workshop's problem at all, and reading them as one number
hides all three.
"""

import frappe

REASONS = [
	# (reason, type, requires a note, description)
	("Break", "Technician", 0,
	 "Scheduled break or shift end. Counts against workshop capacity."),
	("Waiting for Spare", "Waiting on Parts", 0,
	 "The part needed is not on the bench — on order, in transit, or being picked."),
	("Waiting for Customer Confirmation", "Waiting on Customer", 0,
	 "The estimate or a scope change is with the customer and work cannot proceed."),
	("Waiting for Customer Data Backup", "Waiting on Customer", 0,
	 "The customer has not yet consented to, or completed, a data backup."),
	("Waiting for Manager Approval", "Waiting on Approval", 0,
	 "An approval gate — below-cost billing, a spare above threshold — is open."),
	("Waiting for Vendor / RMA", "External", 1,
	 "The device or a part is with a vendor or in an RMA. Say which, and the reference."),
	("Device Sent to Hub", "External", 0,
	 "The device has left this store for a hub or specialist bench."),
	("Diagnosis Inconclusive", "Technician", 1,
	 "The fault could not be reproduced or isolated. Say what was tried."),
	("Equipment Unavailable", "Technician", 1,
	 "A tool, rig or bench needed for this repair is not free. Say which."),
	("Other", "Technician", 1,
	 "Anything not covered above — a note is mandatory so it can be read later."),
]


def execute():
	if not frappe.db.exists("DocType", "GoFix Pause Reason"):
		return

	created = updated = 0
	for name, reason_type, requires_note, description in REASONS:
		values = {
			"reason_name": name,
			"reason_type": reason_type,
			"requires_note": requires_note,
			"description": description,
			"is_active": 1,
		}
		if frappe.db.exists("GoFix Pause Reason", name):
			doc = frappe.get_doc("GoFix Pause Reason", name)
			dirty = False
			for field, value in values.items():
				# Never re-activate or re-word a reason ops has since edited;
				# only fill in what is genuinely blank.
				if field in ("is_active", "reason_name"):
					continue
				if doc.get(field) in (None, "", 0) and value:
					doc.set(field, value)
					dirty = True
			if dirty:
				doc.save(ignore_permissions=True)
				updated += 1
			continue

		doc = frappe.new_doc("GoFix Pause Reason")
		doc.update(values)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		created += 1

	frappe.db.commit()
	frappe.logger("gofix").info(
		f"GoFix: pause reasons seeded — {created} created, {updated} completed"
	)
