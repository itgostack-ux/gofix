# Copyright (c) 2026, GoFix and contributors

"""Make ``Repair Solution.solution_code`` fit to be the document name.

Runs **pre_model_sync**, before the schema change puts a UNIQUE index on
``solution_code`` — a blank or repeated code at that moment aborts the whole
migration. Raw SQL only: the DocType meta this patch runs against is still the
old one.

The rename itself is v05; this patch only guarantees every row has a code that
is non-blank, well-formed and unique.
"""

import frappe

from gofix.gofix_services.doctype.repair_solution.repair_solution import (
	CODE_MAX_LEN,
	slugify_code,
)


def execute():
	if not frappe.db.table_exists("Repair Solution"):
		return
	if not frappe.db.has_column("Repair Solution", "solution_code"):
		return

	rows = frappe.db.sql(
		"""
		SELECT name, solution_name, solution_code
		FROM `tabRepair Solution`
		ORDER BY creation
		""",
		as_dict=True,
	)
	if not rows:
		return

	taken = set()
	fixed = 0

	for row in rows:
		code = slugify_code(row.solution_code or "")
		if not code:
			# Fall back to the label, then to the docname — one of the three is
			# always present on a real row.
			code = slugify_code(row.solution_name or row.name) or "SOLUTION"

		if code in taken:
			base, n = code, 1
			while code in taken:
				n += 1
				suffix = f"-{n}"
				code = f"{base[:CODE_MAX_LEN - len(suffix)].rstrip('-')}{suffix}"

		taken.add(code)
		if code != (row.solution_code or ""):
			frappe.db.sql(
				"UPDATE `tabRepair Solution` SET solution_code = %s WHERE name = %s",
				(code, row.name),
			)
			fixed += 1

	frappe.db.commit()
	frappe.logger("gofix").info(
		f"GoFix: normalised solution_code on {fixed} of {len(rows)} repair solution(s)"
	)
