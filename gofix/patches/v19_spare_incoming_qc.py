"""Incoming QC for spares raised against a repair.

Installs the QC columns on SR Spare Line and the GoFix Spare Event Log that
holds a spare's history, then makes every existing line explicit: a line with
no QC value must read "Pending QC", not empty. An empty value would be read as
"not inspected" by the gate anyway, but leaving it blank means the register
cannot tell "nobody has looked at it" from "this predates QC", and a screen
showing a blank QC column invites someone to assume it passed.

Lines already finished with -- Consumed, Returned, Damaged -- are left alone.
Asking for an inspection of a part that has already been fitted and billed, or
already written off, would put a QC task on the floor for work nobody can do.

Safe to re-run: it only fills values that are still empty.
"""

import frappe


def execute() -> None:
    frappe.reload_doc("gofix_services", "doctype", "gofix_spare_event_log")
    frappe.reload_doc("gofix_services", "doctype", "sr_spare_line")

    if not frappe.db.has_column("SR Spare Line", "qc_status"):
        # reload_doc could not add it (a failed migrate elsewhere); say so
        # rather than silently doing nothing and leaving the gate ungated.
        frappe.log_error(
            title="Spare QC patch: qc_status column missing",
            message="SR Spare Line has no qc_status column after reload_doc.",
        )
        return

    updated = frappe.db.sql(
        """UPDATE `tabSR Spare Line`
              SET qc_status = 'Pending QC'
            WHERE COALESCE(qc_status, '') = ''
              AND COALESCE(status, '') NOT IN ('Consumed', 'Returned', 'Damaged')"""
    )
    frappe.db.commit()
    pending = frappe.db.count("SR Spare Line", {"qc_status": "Pending QC"})
    print(f"SR Spare Line: {pending} line(s) now explicitly Pending QC (updated {updated})")
