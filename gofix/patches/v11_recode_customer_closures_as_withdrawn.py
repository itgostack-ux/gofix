"""Re-code customer-driven closures from Cancelled to Withdrawn.

``repair_closure.CLOSE_OUTCOMES`` used to land both customer outcomes —
"Customer Declined" and "Customer Cancelled" — on ``decision = "Cancelled"``.
Two things read that field and disagree with it:

* the GoFix Rejection Register counts ``Cancelled`` under REFUSED_DECISIONS
  ("ours") and ``Withdrawn`` under CUSTOMER_BACKED_OUT ("theirs"), so every
  customer who walked away was reported as a repair the workshop refused;
* the Job Tracker board has a Withdrawn column reading the same field, which
  could therefore never fill.

Only rows whose ``repairability_status`` is "Customer Declined" are moved:
that value is written by the same closure path, so it identifies a
customer-driven close precisely. A ``Cancelled`` ticket without it was
cancelled by some other route and is left alone.
"""

import frappe


def execute():
	if not frappe.db.table_exists("Service Request"):
		return

	rows = frappe.get_all(
		"Service Request",
		filters={"decision": "Cancelled", "repairability_status": "Customer Declined"},
		pluck="name",
	)
	for name in rows:
		frappe.db.set_value("Service Request", name, "decision", "Withdrawn",
			update_modified=False)

	if rows:
		print(f"v11: re-coded {len(rows)} customer closure(s) from Cancelled to Withdrawn")
