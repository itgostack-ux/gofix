# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

"""Shrink historical 4-letter Company abbreviations to the 2-letter form
that is now the enforced business rule.

Mapping applied
---------------
* ``BMPL`` (BestBuy Mobiles Pvt Ltd) → ``BM``
* ``GSPL`` (GOFIX SOLUTIONS PRIVATE LIMITED) → ``GF``

For every affected company the patch renames the entire trailing
``- <old_abbr>`` suffix on all standard company-abbr-suffixed
DocTypes (Warehouse, Account, Cost Center, Department, POS Profile,
Sales / Purchase Taxes and Charges Template, Terms and Conditions,
Payment Terms Template) via ``frappe.rename_doc``. That cascades the FK
updates into every ledger / stock / transaction table automatically
(Bin, Stock Ledger Entry, GL Entry, Sales / Purchase Invoice Item,
Journal Entry Account, Payment Entry, POS Session tables, custom link
fields, etc.).

To also cover project-generated names like
``BestBuy POS - BMPL - BMPL`` (POS Profile spawned a Cost Center that
appended the abbr twice), the rename loop runs repeatedly per doctype
until no more names end in ``- <old_abbr>`` for that company.

The patch is idempotent:
  * A company whose abbr is already the target 2-char value is skipped.
  * Any doc whose target rename would collide with an existing name is
    left alone and logged (should not occur in practice — old and new
    suffixes never coexist for the same company).

Registered in ``patches.txt`` under ``[post_model_sync]`` so every
environment picks it up on the next ``bench migrate``.
"""

from __future__ import annotations

import frappe

# (Company.name-like search substrings, old_abbr, new_abbr) tuples. The
# name substring is used only as a defensive filter — the primary key is
# the current ``Company.abbr`` value so a company that has already been
# renamed by this patch on this site is skipped cleanly on re-run.
_MAPPING = [
	{"old_abbr": "BMPL", "new_abbr": "BM", "name_hint": "BESTBUY"},
	{"old_abbr": "GSPL", "new_abbr": "GF", "name_hint": "GOFIX"},
]

# Standard ERPNext doctypes that suffix their doc name with " - <abbr>".
# All entries here must have a ``company`` Link field — the batch query
# below filters by ``company=<name>``. Doctypes without a company field
# (Terms and Conditions, Payment Terms Template) are intentionally
# excluded: their names are not company-abbr-suffixed.
_SUFFIX_DOCTYPES = [
	"Account",
	"Cost Center",
	"Warehouse",
	"Department",
	"POS Profile",
	"Sales Taxes and Charges Template",
	"Purchase Taxes and Charges Template",
]


def execute():
	renamed_summary: dict[str, int] = {}
	skipped_summary: dict[str, int] = {}

	for entry in _MAPPING:
		old_abbr = entry["old_abbr"]
		new_abbr = entry["new_abbr"]

		# Resolve target company. Fail-open: skip silently if no company
		# on this site currently uses this old abbr — that means either
		# (a) the site never had it, or (b) this patch already ran.
		company = frappe.db.get_value("Company", {"abbr": old_abbr}, "name")
		if not company:
			frappe.logger("gofix").info(
				f"[rename_company_abbrs] no Company with abbr={old_abbr}; skipping"
			)
			continue

		# Defensive collision guard — refuse to run if another company
		# already owns the target abbr. Would produce name collisions
		# on the suffixed docs.
		conflicting = frappe.db.get_value(
			"Company", {"abbr": new_abbr, "name": ("!=", company)}, "name"
		)
		if conflicting:
			frappe.log_error(
				f"[rename_company_abbrs] abort {old_abbr}->{new_abbr}: "
				f"abbr {new_abbr} already used by Company {conflicting}",
				"rename_company_abbrs_to_2char",
			)
			continue

		renamed = 0
		skipped = 0
		suffix_old = f" - {old_abbr}"
		suffix_new = f" - {new_abbr}"

		# ----------------------------------------------------------------
		# Rename Company.abbr FIRST, then cascade the suffix docs.
		#
		# ERPNext's Cost Center controller (and any future controllers
		# that mirror this pattern) implements ``before_rename`` as::
		#
		#     new_cost_center = get_name_with_abbr(newdn, self.company)
		#
		# ``get_name_with_abbr`` reads the *current* Company.abbr and
		# re-appends it if the new name doesn't already end with it.
		# If Company.abbr is still ``BMPL`` when we try to rename
		# ``X - BMPL`` -> ``X - BM``, the hook will silently rewrite the
		# target back to ``X - BM - BMPL``, and the next iteration of
		# our loop will do it again, producing runaway ``- BM - BM - …
		# - BMPL`` names.
		#
		# Flipping Company.abbr up-front makes the hook a no-op:
		# ``get_name_with_abbr("X - BM", …)`` sees the trailing ``BM``
		# already matches the new abbr and returns the target unchanged.
		# ----------------------------------------------------------------
		frappe.db.set_value(
			"Company", company, "abbr", new_abbr, update_modified=False
		)
		frappe.db.commit()
		frappe.clear_cache()

		for doctype in _SUFFIX_DOCTYPES:
			if not frappe.db.table_exists(doctype):
				continue
			# Defensive: some environments may have doctypes in the list
			# with no ``company`` column (removed by a schema patch,
			# renamed, etc.). Probe first so ``get_all`` doesn't blow up.
			if not frappe.db.has_column(doctype, "company"):
				frappe.logger("gofix").info(
					f"[rename_company_abbrs] {doctype}: no `company` "
					f"column on this site, skipping"
				)
				continue

			# Loop-until-drained to catch names with the suffix repeated
			# (e.g. "BestBuy POS - BMPL - BMPL"). One pass strips one
			# trailing occurrence; keep going while any docs still end
			# in the old suffix for this company. If the batch size
			# does not shrink between iterations we abort — that means
			# a controller hook is re-writing our target and we would
			# otherwise loop forever.
			prev_batch_size: int | None = None
			for _iteration in range(5):
				batch = frappe.get_all(
					doctype,
					filters={
						"company": company,
						"name": ("like", f"%{suffix_old}"),
					},
					pluck="name",
					limit_page_length=0,
				)
				if not batch:
					break
				if prev_batch_size is not None and len(batch) >= prev_batch_size:
					frappe.log_error(
						(
							f"[rename_company_abbrs] {doctype}: batch did not "
							f"shrink ({prev_batch_size} -> {len(batch)}). "
							f"Aborting doctype loop to avoid runaway rename."
						),
						"rename_company_abbrs_to_2char",
					)
					break
				prev_batch_size = len(batch)
				# Rename longest-first so parent-vs-child ordering doesn't
				# leave dangling suffixes on nested-set trees. Sort in
				# Python — Frappe's DatabaseQuery rejects SQL functions
				# in order_by.
				batch.sort(key=len, reverse=True)
				for old_name in batch:
					if not old_name.endswith(suffix_old):
						continue
					new_name = old_name[: -len(suffix_old)] + suffix_new
					if frappe.db.exists(doctype, new_name):
						frappe.logger("gofix").warning(
							f"[rename_company_abbrs] skip {doctype} "
							f"{old_name!r} -> {new_name!r}: target exists"
						)
						skipped += 1
						continue
					try:
						frappe.rename_doc(
							doctype,
							old_name,
							new_name,
							force=True,
							merge=False,
							show_alert=False,
						)
						renamed += 1
					except Exception:
						skipped += 1
						frappe.log_error(
							frappe.get_traceback(),
							(
								f"rename_company_abbrs {doctype} "
								f"{old_name} -> {new_name}"
							),
						)
						frappe.db.rollback()
						continue
				frappe.db.commit()

		# Company.abbr was already flipped above (before the cascade)
		# so that ERPNext's Cost Center before_rename hook uses the new
		# abbr. All Company default Link fields
		# (default_income_account, cost_center, round_off_cost_center,
		# depreciation_cost_center, etc.) were re-pointed by
		# ``frappe.rename_doc`` while the suffix docs were being
		# renamed, so no further action is required here.

		# Best-effort: clear all Frappe caches so any lookups that
		# cached the old abbr expire.
		frappe.clear_cache()

		renamed_summary[f"{old_abbr}->{new_abbr}"] = renamed
		skipped_summary[f"{old_abbr}->{new_abbr}"] = skipped

		frappe.logger("gofix").info(
			f"[rename_company_abbrs] {company}: "
			f"{old_abbr} -> {new_abbr} renamed={renamed} skipped={skipped}"
		)

	if renamed_summary or skipped_summary:
		print(
			f"[gofix] rename_company_abbrs_to_2char: "
			f"renamed={renamed_summary} skipped={skipped_summary}"
		)
