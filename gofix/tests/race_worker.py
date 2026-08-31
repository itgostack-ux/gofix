"""One half of the damage-vs-invoice race. Run twice, concurrently.

  bench execute ... --args '["damage", "<SR>", "<line>"]'
  bench execute ... --args '["invoice", "<SR>", "<line>"]'

Both block on a shared start time so they collide rather than queue.
"""
import time

import frappe
from frappe.utils import get_datetime, now_datetime


def run(role, sr_name, line_name, start_at=None):
	frappe.set_user("Administrator")
	if start_at:
		target = get_datetime(start_at)
		while now_datetime() < target:
			time.sleep(0.01)

	out = {"role": role, "at": str(now_datetime())}
	try:
		if role == "damage":
			from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
				mark_spare_damaged,
			)
			mark_spare_damaged(sr_name, line_name, remarks="race: damaged mid-billing")
			out["result"] = "damaged"
		else:
			sr = frappe.get_doc("Service Request", sr_name)
			rows = sr.get_service_invoice_items() or []
			out["result"] = "billed"
			out["spare_qty"] = sum(
				float(r.get("qty") or 0) for r in rows
				if r.get("item_code") and str(r["item_code"]).startswith("AUDIT")
				or r.get("item_code") == frappe.db.get_value(
					"SR Spare Line", line_name, "spare_item")
			)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		out["result"] = "blocked"
		out["error"] = f"{type(e).__name__}: {str(e)[:120]}"

    # Append rather than overwrite: both halves write to the same file.
	with open("/tmp/claude-1000/-home-palla-erpnext-bench/"
			  "75de5294-00f3-4731-bcc2-284567c83324/scratchpad/race_out.jsonl", "a") as fh:
		fh.write(frappe.as_json(out) + "\n")
	print("RACE", out)
