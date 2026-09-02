# Copyright (c) 2026, GoStack and contributors

"""Move spare compatibility from the sales SKU to the device model family.

``GoFix Spare Compatible Model.device_model`` was a Link → Item, so it pointed
at a colour/storage sales variant ("Apple iPhone 15 128GB Blue"). A screen or
battery fits every variant of a model, so compatibility belongs on the MODEL
family (CH Model "Apple iPhone 15"), with colour as a separate qualifier used
only for cosmetic parts. See the design note.

This patch, run after the field is re-pointed to CH Model:
  1. Backfills every existing row: the stored model NAME → the device-family
     CH Model docname. Rows already holding a valid CH Model name are left; a
     value that resolves to no device-family model is logged and left untouched
     so nothing is silently dropped.
  2. Marks the four cosmetic sub-categories colour-specific.
  3. Seeds ``colour`` on the compatibility rows of colour-specific spares,
     derived from the spare Item's own name — but only when the trailing word
     is a real colour, never a quality tier ("Original"/"Genuine").

Idempotent: a row already carrying a CH Model docname, or a colour, is skipped.
"""

import frappe

COSMETIC_SUB_CATEGORIES = (
	"Mobile Spares-Back Door",
	"Mobile Spares-Back Glass",
	"Mobile Spares-Frame",
	"Mobile Spares-Housing Set",
)
_BASE_COLOURS = (
	"Black", "White", "Blue", "Red", "Green", "Pink", "Purple", "Gold",
	"Silver", "Grey", "Gray", "Yellow", "Orange", "Titanium", "Graphite",
	"Teal", "Coral", "Lavender", "Cream", "Bronze", "Copper", "Rose",
)


def _resolve_device_family_model(value: str) -> str | None:
	"""The one device-family CH Model whose model_name equals `value`."""
	if not value:
		return None
	# Already a CH Model docname? keep it.
	if frappe.db.exists("CH Model", value):
		return value
	rows = frappe.db.sql(
		"""SELECT name, sub_category FROM `tabCH Model` WHERE model_name = %s""",
		value, as_dict=True,
	)
	device = [r for r in rows if "Spares" not in (r.sub_category or "")]
	return device[0].name if len(device) == 1 else None


def _colour_from_name(item_name: str) -> str | None:
	tokens = (item_name or "").replace("-", " ").split()
	for word in reversed(tokens):
		for colour in _BASE_COLOURS:
			if word.strip().lower() == colour.lower():
				return colour
	return None


def execute():
	meta = frappe.get_meta("GoFix Spare Compatible Model")
	if not meta.get_field("device_model") or meta.get_field("device_model").options != "CH Model":
		# Schema not synced yet — nothing safe to do.
		return

	# ── 1. Backfill device_model → CH Model docname ──────────────────────────
	rewired, unresolved = 0, []
	for row in frappe.db.sql(
		"""SELECT name, device_model, device_model_name FROM `tabGoFix Spare Compatible Model`""",
		as_dict=True,
	):
		current = row.device_model
		if current and frappe.db.exists("CH Model", current):
			continue
		resolved = _resolve_device_family_model(current) or _resolve_device_family_model(row.device_model_name)
		if not resolved:
			unresolved.append(current)
			continue
		updates = {"device_model": resolved}
		if not row.device_model_name:
			updates["device_model_name"] = frappe.db.get_value("CH Model", resolved, "model_name")
		frappe.db.set_value("GoFix Spare Compatible Model", row.name, updates, update_modified=False)
		rewired += 1

	# ── 2. Flag the cosmetic sub-categories ──────────────────────────────────
	flagged = 0
	if frappe.db.has_column("CH Sub Category", "is_colour_specific"):
		for sub in COSMETIC_SUB_CATEGORIES:
			if frappe.db.exists("CH Sub Category", sub) and not frappe.db.get_value(
				"CH Sub Category", sub, "is_colour_specific"
			):
				frappe.db.set_value("CH Sub Category", sub, "is_colour_specific", 1, update_modified=False)
				flagged += 1

	# ── 3. Seed colour on colour-specific spares' rows ───────────────────────
	coloured = 0
	if frappe.db.has_column("GoFix Spare Compatible Model", "colour"):
		spare_rows = frappe.db.sql(
			"""
			SELECT scm.name, scm.colour, i.item_name
			FROM `tabGoFix Spare Compatible Model` scm
			JOIN `tabItem` i ON i.name = scm.parent AND scm.parenttype = 'Item'
			WHERE i.ch_sub_category IN %(subs)s
			""",
			{"subs": COSMETIC_SUB_CATEGORIES},
			as_dict=True,
		)
		for row in spare_rows:
			if (row.colour or "").strip():
				continue
			colour = _colour_from_name(row.item_name)
			if colour:
				frappe.db.set_value(
					"GoFix Spare Compatible Model", row.name, "colour", colour, update_modified=False
				)
				coloured += 1

	frappe.db.commit()
	frappe.logger("gofix").info(
		f"GoFix spare compat: re-pointed {rewired} row(s) to CH Model; "
		f"flagged {flagged} cosmetic sub-categories; seeded colour on {coloured} row(s)"
		+ (f"; UNRESOLVED model names left as-is: {sorted(set(unresolved))[:10]}" if unresolved else "")
	)
