# Copyright (c) 2026, GoFix and contributors

"""Add the missing "Swapping Board" repair and route its spares to it.

The market rate card in v02 has always carried a rate for *Swapping Board*
(Rs 1,500, 90-day warranty) but the solution catalogue never contained such a
repair, so the rate was silently skipped and the 14 real swapping-board spare
Items were mapped to **Board-Level Repair** instead.

They are different jobs. BRD-REP is component-level micro-soldering: 240
minutes of an Expert technician, Rs 2,000 (Rs 3,600 on premium brands).
Swapping a board is fitting a working donor/replacement PCB — faster, lower
skill, and priced accordingly. Billing a swap as a board-level repair
overcharges the customer and books four hours of the workshop's scarcest grade
against a parts swap.

Safe to run: no job has ever consumed one of these spares, so nothing
historical is repriced.
"""

import frappe

from gofix.catalogue_sync import resolve_solution

SOLUTION_CODE = "BRD-SWP"
SOLUTION_NAME = "Swapping Board"
ISSUE_CATEGORY = "Board Diagnosis"
SPARE_SUB_CATEGORY = "Mobile Spares-Swapping Board"
# A swap is a phone/tablet job here; the catalogue's boards are all handsets.
APPLIES_TO = ["Smart Phones", "Tablets"]


def execute():
	if not frappe.db.table_exists("Repair Solution"):
		return
	if not frappe.db.exists("Issue Category", ISSUE_CATEGORY):
		return

	name = _ensure_solution()
	if not name:
		return

	_remap_spares(name)
	_price_it(name)
	frappe.db.commit()


def _ensure_solution():
	existing = resolve_solution(SOLUTION_CODE) or resolve_solution(SOLUTION_NAME, ISSUE_CATEGORY)
	if existing:
		return existing

	doc = frappe.new_doc("Repair Solution")
	doc.solution_name = SOLUTION_NAME
	doc.solution_code = SOLUTION_CODE
	doc.issue_category = ISSUE_CATEGORY
	doc.description = (
		"Fit a working replacement/donor mainboard. Distinct from Board-Level "
		"Repair, which reworks components on the customer's own board."
	)
	doc.estimated_minutes = 120
	doc.requires_spare = 1
	doc.skill_level = "Advanced"
	doc.is_active = 1
	doc.is_billable = 1
	grade = frappe.db.get_value("Technician Grade", {"grade_name": ["like", "%Advanced%"]}, "name")
	if grade:
		doc.minimum_grade = grade
	if frappe.get_meta("Repair Solution").get_field("applies_to"):
		for category in APPLIES_TO:
			if frappe.db.exists("CH Category", category):
				doc.append("applies_to", {"device_category": category})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	frappe.logger("gofix").info(f"GoFix: created repair solution {doc.name}")
	return doc.name


def _remap_spares(solution):
	"""Point the swapping-board spares at the swap instead of the rework.

	Only rows that still say Board-Level Repair are touched, and only for spares
	in the swapping-board sub-category — an ops-set mapping on any other spare
	is left alone. The Item is the master of this relationship, so the Item's
	own child rows are rewritten and the mirror rebuilt from them.
	"""
	board_repair = resolve_solution("BRD-REP") or resolve_solution("Board-Level Repair")
	if not board_repair:
		return

	items = frappe.get_all(
		"Item", filters={"ch_sub_category": SPARE_SUB_CATEGORY}, pluck="name"
	)
	if not items:
		return

	from gofix.catalogue_sync import sync_spare_mappings_from_item

	moved = 0
	for item_code in items:
		item = frappe.get_doc("Item", item_code)
		dirty = False
		for row in item.get("gofix_repair_solutions") or []:
			if row.repair_solution == board_repair:
				row.repair_solution = solution
				row.issue_category = ISSUE_CATEGORY
				dirty = True
		if not dirty:
			continue
		item.flags.ignore_permissions = True
		item.flags.ignore_validate_update_after_submit = True
		item.save(ignore_permissions=True)
		sync_spare_mappings_from_item(item)
		moved += 1

	frappe.logger("gofix").info(
		f"GoFix: re-pointed {moved} swapping-board spare(s) from {board_repair} to {solution}"
	)


def _price_it(solution):
	"""Let the existing market rate card apply to the new solution."""
	from gofix.patches.v02_seed_market_standard_pricing import (
		_seed_pricing_rules,
		_seed_workmanship_warranty,
	)

	_seed_workmanship_warranty()
	_seed_pricing_rules()
	n = frappe.db.count("GoFix Pricing Rule", {"repair_solution": solution})
	frappe.logger("gofix").info(f"GoFix: {solution} now has {n} pricing rule(s)")
