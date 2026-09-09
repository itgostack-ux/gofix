"""Rebuild Sales Invoice.outstanding_amount from the ledger where they disagree.

Sixty-one submitted invoices carried an `outstanding_amount` that did not match
their own General Ledger and Payment Ledger entries -- 26 at Bestbuy and 35 at
GoFix, understating receivables by Rs 362,476 between them. The GL and the PLE
agreed with each other; only the cached field on the invoice had been zeroed,
with no Payment Entry behind it, leaving 35 invoices reading "Unpaid" with
nothing outstanding.

The books were never wrong. What was wrong is the number the business reads:
aged receivables, the Paid/Unpaid badge and any collections chase all take the
cached field, so a customer who owed money showed as owing none.

This recomputes that field from the ledger using ERPNext's own
`update_voucher_outstanding`, which is the function the framework calls after
every payment. It touches no GL entry and creates no document; it only makes the
invoice agree with the ledger that was already right.

Re-runnable: an invoice that already agrees is skipped.
"""

import frappe
from frappe.utils import flt


def divergent_invoices() -> list:
	"""Invoices whose cached outstanding disagrees with their Debtors balance."""
	return frappe.db.sql("""
		SELECT si.name, si.company, si.customer, si.debit_to, si.status,
		       si.outstanding_amount AS says, gl.bal AS ledger_says
		FROM `tabSales Invoice` si
		JOIN (SELECT against_voucher, SUM(debit) - SUM(credit) AS bal
		      FROM `tabGL Entry`
		      WHERE is_cancelled = 0 AND account LIKE 'Debtors%'
		      GROUP BY against_voucher) gl ON gl.against_voucher = si.name
		WHERE si.docstatus = 1 AND ABS(gl.bal - si.outstanding_amount) > 0.01
		ORDER BY si.company, si.name""", as_dict=True)


def execute(dry_run: bool = False) -> dict:
	from erpnext.accounts.utils import update_voucher_outstanding

	rows = divergent_invoices()
	repaired, failed, gap = [], [], 0.0
	for r in rows:
		gap += flt(r.ledger_says) - flt(r.says)
		if dry_run:
			continue
		try:
			update_voucher_outstanding("Sales Invoice", r.name, r.debit_to,
			                           "Customer", r.customer)
			after = flt(frappe.db.get_value("Sales Invoice", r.name,
			                                "outstanding_amount"))
			if abs(after - flt(r.ledger_says)) < 0.01:
				repaired.append(r.name)
			else:
				failed.append((r.name, after, r.ledger_says))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"receivable repair {r.name}")
			failed.append((r.name, None, r.ledger_says))

	if not dry_run:
		frappe.db.commit()
	return {"found": len(rows), "understated_by": round(gap, 2),
	        "repaired": len(repaired), "failed": failed}
