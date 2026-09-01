# Copyright (c) 2026, GoFix and contributors

"""Provision the service Items that v01 was never able to create.

``v01_link_service_catalogue_to_items`` gives every billable Repair Solution its
own non-stock service Item. It logs and continues when one fails, which is right
-- but it means that on a site where *every* attempt failed the patch still
completed, was written to the Patch Log, and will never run again.

That is exactly what happened wherever the repair-labour sub-category did not
exist yet: it is seeded by an ``after_migrate`` hook, while v01 runs during
``run_schema_updates``, so the Items were built with no HSN and india_compliance
rejected all of them. The catalogue was left with no service Items at all and
every repair invoicing through the one generic line again -- silently, because
v01 had already been recorded as done.

``ensure_service_item`` now seeds that taxonomy on demand, so this re-runs the
provisioning for anything still missing. Idempotent: a solution that already has
its Item is skipped.
"""

import frappe

from gofix.catalogue_sync import ensure_service_item


def execute():
	if not frappe.db.table_exists("Repair Solution"):
		return

	pending = [
		r.name
		for r in frappe.db.sql(
			"""SELECT name, service_item FROM `tabRepair Solution`
			   WHERE IFNULL(is_billable, 1) = 1""",
			as_dict=True,
		)
		if not r.service_item or not frappe.db.exists("Item", r.service_item)
	]
	if not pending:
		return

	created, failed = 0, []
	for name in pending:
		try:
			if ensure_service_item(name):
				created += 1
		except Exception:
			# A throw queues its message even when caught; leaving it there makes
			# every later save in this migrate replay the popup.
			failed.append(name)
			frappe.clear_messages()
			frappe.log_error(
				frappe.get_traceback(), f"GoFix: could not provision service item for {name}"
			)

	frappe.db.commit()
	frappe.logger("gofix").info(
		f"GoFix: back-filled {created} repair service item(s) of {len(pending)} missing"
		+ (f"; still failing: {failed}" if failed else "")
	)
