"""Backfill actual technician time from Job Assignment status history."""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Job Assignment"):
		return

	from gofix.gofix_services.doctype.job_assignment.job_assignment import (
		reconcile_job_assignment_actual_hours,
	)

	summary = reconcile_job_assignment_actual_hours()
	frappe.logger("gofix").info(
		"Job Assignment actual-hours backfill complete: %s",
		summary,
	)
	print(f"Job Assignment actual-hours backfill complete: {summary}")
