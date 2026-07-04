"""Adopt the ``GF-<SLUG>`` convention for every store owned by a
``Company`` with ``gofix_enabled = 1``.

Background
----------
Gogizmo stores under BMPL have always been named ``GG-<SLUG>``
(``GG-DOVETON``, ``GG-ANNANAGAR``, …). The prefix is a brand marker —
tablets, POS profiles, reports and dashboards all key off it. GoFix
Solutions Pvt Ltd (``GSPL``) started out with the generic
``STO-GSPL-CHENNA-####`` autoname because the ``CHStore.autoname``
generator didn't yet know about the GoFix brand.

This patch backfills the missing convention: every GSPL (or any other
``gofix_enabled``) store is renamed to ``GF-<STORE_NAME_SLUG>`` and its
warehouse tree (Store Group + Sellable / Damaged / Demo / Buyback leaves)
is renamed in lock-step. All foreign-key references cascade
automatically via ``frappe.rename_doc``.

Idempotency
-----------
Safe to re-run. Stores already starting with ``GF-`` are skipped.
Warehouse renames are skipped when the target name already exists.

Failure handling
----------------
Each store is renamed inside its own try/except with ``frappe.log_error``
on failure. One bad store never blocks the rest of the batch.
"""

from __future__ import annotations

import frappe

from ch_item_master.ch_core.doctype.ch_store.ch_store import (
	_GOFIX_STORE_PREFIX,
	_slugify_store_name,
)


def _unique_store_code(store_name: str) -> str | None:
	"""Return an unused ``GF-<SLUG>`` code for the given store name.

	Returns ``None`` if the store name yields an empty slug (defensive
	guard — CHStore.validate already forbids blank names).
	"""
	slug = _slugify_store_name(store_name)
	if not slug:
		return None
	base = f"{_GOFIX_STORE_PREFIX}{slug}"
	if not frappe.db.exists("CH Store", base):
		return base
	seq = 2
	while frappe.db.exists("CH Store", f"{base}-{seq}"):
		seq += 1
	return f"{base}-{seq}"


def _company_abbr(company: str) -> str | None:
	return frappe.db.get_value("Company", company, "abbr")


def _rename_store_warehouses(old_code: str, new_code: str, company: str) -> list[dict]:
	"""Rename every warehouse whose name is composed from the old store code.

	Returns a list of ``{old, new, status}`` dicts describing each attempt
	so the outer loop can log a per-warehouse trail.
	"""
	abbr = _company_abbr(company)
	suffix = f" - {abbr}" if abbr else ""

	# Warehouses derived from a store code follow a strict pattern:
	#   "<CODE><anything> - <ABBR>"
	# We match by prefix rather than by ``ch_store`` because the Store Group
	# warehouse historically wasn't stamped with that column on every site.
	like_pattern = f"{old_code}%{suffix}"
	warehouses = frappe.get_all(
		"Warehouse",
		filters={"company": company, "name": ("like", like_pattern)},
		fields=["name", "warehouse_name"],
		order_by="is_group desc, name asc",
	)

	results: list[dict] = []
	for wh in warehouses:
		# Compute the replacement name by swapping the store-code segment.
		# Everything AFTER the store code (the "-Sellable"/-Damaged" tail
		# plus the company suffix) is preserved verbatim.
		if not wh.name.startswith(old_code):
			continue
		tail = wh.name[len(old_code):]  # e.g. "-Sellable - GSPL" or " - GSPL"
		new_name = f"{new_code}{tail}"
		if new_name == wh.name:
			results.append({"old": wh.name, "new": new_name, "status": "unchanged"})
			continue
		if frappe.db.exists("Warehouse", new_name):
			results.append({"old": wh.name, "new": new_name, "status": "target_exists"})
			continue
		try:
			frappe.rename_doc(
				"Warehouse",
				wh.name,
				new_name,
				force=True,
				merge=False,
				show_alert=False,
			)
			# ``warehouse_name`` is a display field that Frappe stamps from
			# whatever the user typed; rename_doc updates ``name`` but
			# leaves warehouse_name at its old value. Re-derive it so the
			# tree view + reports match the new name.
			new_wh_display = new_name[: -len(f" - {_company_abbr(company)}")] if _company_abbr(company) and new_name.endswith(f" - {_company_abbr(company)}") else new_name
			frappe.db.set_value(
				"Warehouse", new_name, "warehouse_name", new_wh_display,
				update_modified=False,
			)
			results.append({"old": wh.name, "new": new_name, "status": "renamed"})
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"rename_gspl_stores_to_gf_prefix: warehouse {wh.name} -> {new_name}",
			)
			results.append({"old": wh.name, "new": new_name, "status": "error"})
	return results


def execute():
	# Locate every store belonging to a gofix-enabled company that doesn't
	# already carry the GF- prefix. If ``gofix_enabled`` doesn't exist on
	# the Company doctype yet (fresh install before the field is created)
	# the query simply returns nothing — patch becomes a no-op.
	try:
		gofix_companies = [
			row.name
			for row in frappe.get_all(
				"Company",
				filters={"gofix_enabled": 1},
				fields=["name"],
			)
		]
	except Exception:
		return

	if not gofix_companies:
		return

	stores = frappe.get_all(
		"CH Store",
		filters={
			"company": ("in", gofix_companies),
			"store_code": ("not like", f"{_GOFIX_STORE_PREFIX}%"),
		},
		fields=["name", "store_code", "store_name", "company", "warehouse", "warehouse_group"],
		order_by="name asc",
	)

	if not stores:
		return

	renamed = 0
	skipped: list[str] = []
	for store in stores:
		old_code = store.name
		new_code = _unique_store_code(store.store_name or "")
		if not new_code:
			skipped.append(f"{old_code}: empty slug for store_name={store.store_name!r}")
			continue
		if new_code == old_code:
			continue

		try:
			# 1. Rename the warehouse tree FIRST so the CH Store.warehouse /
			#    warehouse_group Link fields end up pointing at the new
			#    warehouse names by the time the CH Store rename cascade
			#    updates every referrer.
			_rename_store_warehouses(old_code, new_code, store.company)

			# 2. Rename the CH Store document itself. Frappe cascades this
			#    to every DocType with a Link → CH Store (POS Profile
			#    extension, CH POS Session, CH Cash Drop, CH Device Master,
			#    CH User Scope Store, CH Route Stop, CH Transfer Manifest,
			#    Material Request / Stock Entry custom fields, and so on).
			frappe.rename_doc(
				"CH Store",
				old_code,
				new_code,
				force=True,
				merge=False,
				show_alert=False,
			)

			# 3. Sync the ``store_code`` scalar field to the new name.
			#    rename_doc updates ``name`` but the doctype keeps
			#    store_code as a separate persisted column that other
			#    reports & search paths read directly.
			frappe.db.set_value(
				"CH Store", new_code, "store_code", new_code,
				update_modified=False,
			)

			# 4. The ``ch_store`` column on every warehouse under this
			#    store was pointing at the OLD name. rename_doc for
			#    warehouses only touches the warehouse's own ``name``, not
			#    the tag column, so refresh it explicitly.
			frappe.db.sql(
				"UPDATE `tabWarehouse` SET ch_store = %s WHERE ch_store = %s",
				(new_code, old_code),
			)

			frappe.db.commit()
			renamed += 1
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				frappe.get_traceback(),
				f"rename_gspl_stores_to_gf_prefix: {old_code} -> {new_code}",
			)
			skipped.append(f"{old_code}: exception (see error log)")

	# Emit a single summary line so the migration output is grep-able
	# but doesn't spam per-store output on large sites.
	print(
		f"[gofix] rename_gspl_stores_to_gf_prefix: renamed={renamed} "
		f"skipped={len(skipped)}"
	)
	for line in skipped:
		print(f"  - {line}")
