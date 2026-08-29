"""Connect the GoFix service catalogue to the Item master.

Two gaps this closes, both of which left the catalogue half-wired:

1. Every Repair Solution invoiced through one generic GOFIX-REPAIR-SERVICE
   line, so no repair had its own SAC code, price list entry or revenue line.
   Each billable solution now owns a non-stock service Item under the
   repair-labour sub-category.

2. ``Solution Spare Mapping`` was completely empty while 1,000+ spare Items
   already carried GoFix compatibility rows -- so ``requires_spare`` solutions
   had no part to offer. Spares now declare the repairs they serve on the Item
   itself (``Item.gofix_repair_solutions``), seeded here from the spare's
   CH Sub Category, and mirrored into Solution Spare Mapping by
   ``gofix.catalogue_sync``.

The sub-category -> solution seed below is a STARTING POINT derived from the
catalogue's own naming. It is applied only to Items that have no mapping yet,
so any correction made in the UI survives a re-run.
"""

import frappe

from gofix.catalogue_sync import ensure_service_item, sync_spare_mappings_from_item

# spare CH Sub Category -> the repair that consumes it
SUB_CATEGORY_TO_SOLUTION = {
	"Mobile Spares-Displays": "Screen Replacement",
	"Mobile Spares-Batteries": "Battery Replacement",
	"Mobile Spares-Outer Keys": "Button / Flex Replacement",
	"Mobile Spares-Inner Strip": "Button / Flex Replacement",
	"Mobile Spares-Volume Strip": "Button / Flex Replacement",
	"Mobile Spares-Power Button Strip": "Button / Flex Replacement",
	"Mobile Spares-Housing Set": "Body / Frame Repair",
	"Mobile Spares-Frame": "Body / Frame Repair",
	"Mobile Spares-Back Door": "Back Panel Replacement",
	"Mobile Spares-Back Glass": "Back Panel Replacement",
	"Mobile Spares-Charging Board": "Charging Port Replacement",
	"Mobile Spares-Charging Strip": "Charging Port Replacement",
	"Mobile Spares-Charging Pin": "Charging Port Replacement",
	"Mobile Spares-Sub Board Strip": "Charging Port Replacement",
	"Mobile Spares-Ear Speaker": "Speaker Replacement",
	"Mobile Spares-Ringer": "Speaker Replacement",
	"Mobile Spares-Speaker": "Speaker Replacement",
	"Mobile Spares-Rear Cameras": "Camera Replacement",
	"Mobile Spares-Front Cameras": "Camera Replacement",
	"Mobile Spares-Camera Lens": "Camera Glass Replacement",
	"Mobile Spares-Finger Print Sensor": "Fingerprint Sensor Replacement",
	"Mobile Spares-Swapping Board": "Board-Level Repair",
	"Mobile Spares-Board Connector": "Board-Level Repair",
	"Mobile Spares-Network Strip": "Antenna / Network IC Repair",
	# Deliberately unmapped -- no repair in the catalogue consumes it yet:
	#   Mobile Spares-Sim Tray
}


def execute():
	if not frappe.db.exists("DocType", "Repair Solution"):
		return

	_provision_service_items()
	_seed_item_solution_links()
	frappe.db.commit()


def _provision_service_items():
	created = 0
	for name in frappe.get_all("Repair Solution", pluck="name"):
		solution = frappe.get_doc("Repair Solution", name)
		if solution.get("service_item"):
			continue
		try:
			if ensure_service_item(solution):
				created += 1
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"GoFix: could not provision service item for {name}"
			)
	frappe.logger("gofix").info(f"GoFix: provisioned {created} repair service item(s)")


def _seed_item_solution_links():
	if not frappe.get_meta("Item").get_field("gofix_repair_solutions"):
		return

	valid = {
		sub: sol
		for sub, sol in SUB_CATEGORY_TO_SOLUTION.items()
		if frappe.db.exists("Repair Solution", sol)
	}
	if not valid:
		return

	# only spares that actually declare GoFix compatibility, and only those with
	# nothing mapped yet -- never overwrite a decision made in the UI
	rows = frappe.db.sql(
		"""
		SELECT i.name, i.ch_sub_category
		FROM `tabItem` i
		WHERE IFNULL(i.disabled, 0) = 0
		  AND i.ch_sub_category IN %(subs)s
		  AND EXISTS (
			SELECT 1 FROM `tabGoFix Spare Compatible Model` m
			WHERE m.parent = i.name AND m.parenttype = 'Item'
		  )
		  AND NOT EXISTS (
			SELECT 1 FROM `tabGoFix Item Repair Solution` r
			WHERE r.parent = i.name AND r.parenttype = 'Item'
		  )
		""",
		{"subs": tuple(valid)},
		as_dict=True,
	)

	linked = 0
	for row in rows:
		solution = valid.get(row.ch_sub_category)
		if not solution:
			continue
		try:
			item = frappe.get_doc("Item", row.name)
			item.append("gofix_repair_solutions", {
				"repair_solution": solution,
				"issue_category": frappe.db.get_value(
					"Repair Solution", solution, "issue_category"
				),
				"default_qty": 1,
				"is_mandatory": 1,
			})
			item.flags.ignore_permissions = True
			item.flags.ignore_validate_update_after_submit = True
			item.save(ignore_permissions=True)
			sync_spare_mappings_from_item(item)
			linked += 1
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), f"GoFix: could not link spare {row.name}"
			)
	frappe.logger("gofix").info(f"GoFix: linked {linked} spare Item(s) to a repair solution")
