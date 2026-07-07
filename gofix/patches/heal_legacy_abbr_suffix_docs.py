# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Heal sites left with a split company-abbr namespace (``X - BMPL`` AND
``X - BM`` coexisting).

``rename_company_abbrs_to_2char`` handles the clean case — a company whose
abbr is still the legacy 4-char value gets its abbr flipped and every
suffixed doc renamed. But two loopholes leave sites with BOTH trees:

1. The patch runs once. If the legacy company (or its warehouses) appeared
   AFTER the patch executed on that site — e.g. a fresh site seeded from a
   baseline JSON that still carried ``abbr: BMPL`` — nothing re-heals it,
   and the Location Hierarchy / Warehouse tree shows two roots
   ("All Warehouses - BMPL" with the Chennai Hub under it, plus
   "All Warehouses - BM").
2. The original rename skips any doc whose target name already exists
   ("Chennai - Hub - BMPL" vs an already-created "Chennai - Hub - BM"),
   so partially-migrated sites stay split forever.

This patch closes both:

* Re-runs the original rename keyed on the CURRENT company (found via the
  legacy abbr OR the new abbr), so legacy-suffixed docs are swept even when
  ``Company.abbr`` was already flipped.
* When the rename target exists for the SAME company, merges the legacy doc
  into it via ``frappe.rename_doc(..., merge=True)`` (leaf docs first so
  nested-set parents drain naturally). Failures are logged and skipped —
  a stubborn doc must never abort the migration.

Idempotent: a site with no legacy-suffixed docs is a no-op.
"""

from __future__ import annotations

import frappe

from gofix.patches.rename_company_abbrs_to_2char import (
	_MAPPING,
	_SUFFIX_DOCTYPES,
	execute as _rename_execute,
)


def execute():
	# Pass 1 — the original patch handles companies still on the legacy
	# abbr (flips abbr, renames collision-free docs). Safe to re-run.
	_rename_execute()

	# Pass 2 — sweep legacy-suffixed docs that pass 1 could not touch:
	# either the company was already on the new abbr (original patch
	# skips the whole mapping) or the target name already existed
	# (original patch skips the doc). Merge into the target when it
	# belongs to the same company.
	for entry in _MAPPING:
		old_abbr = entry["old_abbr"]
		new_abbr = entry["new_abbr"]

		company = (
			frappe.db.get_value("Company", {"abbr": old_abbr}, "name")
			or frappe.db.get_value("Company", {"abbr": new_abbr}, "name")
		)
		if not company:
			continue

		suffix_old = f" - {old_abbr}"
		suffix_new = f" - {new_abbr}"
		renamed = merged = skipped = 0

		for doctype in _SUFFIX_DOCTYPES:
			if not frappe.db.table_exists(doctype):
				continue
			if not frappe.db.has_column(doctype, "company"):
				continue

			batch = frappe.get_all(
				doctype,
				filters={"company": company, "name": ("like", f"%{suffix_old}")},
				pluck="name",
				limit_page_length=0,
			)
			if not batch:
				continue
			# Leaves before parents: for tree doctypes (Warehouse, Account,
			# Cost Center) merging a group is only possible once its
			# children are gone. Longest name first is a cheap proxy that
			# also handles repeated suffixes.
			batch.sort(key=len, reverse=True)

			for old_name in batch:
				if not old_name.endswith(suffix_old):
					continue
				new_name = old_name[: -len(suffix_old)] + suffix_new
				target_exists = frappe.db.exists(doctype, new_name)
				if target_exists:
					target_company = frappe.db.get_value(doctype, new_name, "company")
					if target_company != company:
						skipped += 1
						frappe.logger("gofix").warning(
							f"[heal_legacy_abbr] skip {doctype} {old_name!r}: "
							f"target {new_name!r} belongs to {target_company!r}"
						)
						continue
				try:
					frappe.rename_doc(
						doctype,
						old_name,
						new_name,
						force=True,
						merge=bool(target_exists),
						show_alert=False,
					)
					if target_exists:
						merged += 1
					else:
						renamed += 1
					frappe.db.commit()
				except Exception:
					skipped += 1
					frappe.db.rollback()
					frappe.log_error(
						frappe.get_traceback(),
						f"heal_legacy_abbr {doctype} {old_name} -> {new_name}",
					)

		if renamed or merged or skipped:
			frappe.clear_cache()
			print(
				f"[gofix] heal_legacy_abbr_suffix_docs {old_abbr}->{new_abbr} "
				f"({company}): renamed={renamed} merged={merged} skipped={skipped}"
			)
