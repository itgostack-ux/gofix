"""Held time gets its own track on the ticket timeline.

The operations stage does not change when a repair is paused -- the ticket
stays in Repair -- so the hours a job spent waiting for a part were charged to
the bench. A board reading that cannot tell a slow repair from a stalled one,
and only one of those is something anybody can act on.

This installs the "Hold" option on GoFix Status Log.event_type. Nothing is
backfilled: the holds that already happened were never recorded, and inventing
them from paused_at would put times on the timeline that nobody observed.
Tickets held from now on carry the track.

Safe to re-run.
"""

import frappe


def execute() -> None:
    frappe.reload_doc("gofix_services", "doctype", "gofix_status_log")

    meta = frappe.get_meta("GoFix Status Log", cached=False)
    field = meta.get_field("event_type")
    if not field or "Hold" not in (field.options or ""):
        frappe.log_error(
            title="Hold track patch: event_type option missing",
            message="GoFix Status Log.event_type has no Hold option after reload_doc.",
        )
        return

    held = frappe.db.count("SR Solution Line", {"status": "On Hold"})
    print(f"GoFix Status Log: Hold track available ({held} solution line(s) currently On Hold)")
