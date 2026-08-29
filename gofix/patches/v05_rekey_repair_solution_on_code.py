# Copyright (c) 2026, GoFix and contributors

"""Rename every Repair Solution from its label to its ``solution_code``.

``Repair Solution`` used to autoname on ``solution_name``, so the label WAS the
primary key and could exist exactly once system-wide. "Display Change" under
both Screen & Display and Physical Damage was therefore unrepresentable, and
the Ops Hub's quick-create dead-ended on it.

``frappe.rename_doc`` repoints every Link field, dynamic link, attachment and
version row, so SR solution lines, spare mappings, pricing rules and job cards
follow the rename. The ``GFR-*`` service Items are deliberately untouched:
``_service_item_code`` already derives them from ``solution_code``, so their
codes are the same before and after.

Idempotent — a row already named by its code is skipped.
"""

import frappe
# frappe.rename_doc is a narrower wrapper that does not accept
# ignore_permissions; a patch runs as Administrator but not necessarily with
# a permitted user context, so call the model function directly.
from frappe.model.rename_doc import rename_doc

from gofix.catalogue_sync import _service_item_code


def execute():
	if not frappe.db.table_exists("Repair Solution"):
		return

	rows = frappe.db.sql(
		"SELECT name, solution_code, service_item FROM `tabRepair Solution` ORDER BY creation",
		as_dict=True,
	)
	pending = [r for r in rows if r.solution_code and r.name != r.solution_code]
	if not pending:
		return

	# A code that is already some OTHER row's docname would collide mid-run.
	# v04 guarantees codes are unique among themselves; this guards the case
	# where a label happens to equal another solution's code.
	existing_names = {r.name for r in rows}
	renamed, skipped = 0, []

	for row in pending:
		if row.solution_code in existing_names and row.solution_code != row.name:
			skipped.append(f"{row.name} -> {row.solution_code} (name already taken)")
			continue
		try:
			rename_doc(
				"Repair Solution",
				row.name,
				row.solution_code,
				force=True,
				merge=False,
				ignore_permissions=True,
				show_alert=False,
				rebuild_search=False,
			)
			existing_names.discard(row.name)
			existing_names.add(row.solution_code)
			renamed += 1
		except Exception:
			skipped.append(row.name)
			frappe.log_error(
				frappe.get_traceback(),
				f"GoFix: could not re-key Repair Solution {row.name}",
			)

	frappe.db.commit()

	# The service Item must still be the one the solution was invoicing before.
	drifted = 0
	for row in frappe.db.sql(
		"SELECT name, solution_name, solution_code, service_item "
		"FROM `tabRepair Solution` WHERE IFNULL(service_item, '') != ''",
		as_dict=True,
	):
		expected = _service_item_code(row)
		if row.service_item != expected:
			drifted += 1
	if drifted:
		frappe.logger("gofix").warning(
			f"GoFix: {drifted} repair solution(s) point at a service Item that no longer "
			"matches their code — left as-is so no invoice history is broken"
		)

	frappe.logger("gofix").info(
		f"GoFix: re-keyed {renamed} repair solution(s) onto solution_code"
		+ (f"; skipped {len(skipped)}: {skipped}" if skipped else "")
	)
