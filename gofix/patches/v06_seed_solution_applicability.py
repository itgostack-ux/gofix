# Copyright (c) 2026, GoFix and contributors

"""Declare which device families each shipped repair applies to.

Every solution used to be offered for every device that shared its Issue
Category, so a Samsung phone under Physical Damage was offered Hinge Repair and
Strap Replacement — a laptop operation and a watch operation.

Only genuinely device-specific repairs get rows here. Anything performed across
the board (diagnostics, battery, board work, liquid damage, data) is left with
no rows, which means universal — the safe default, and it keeps this patch from
hiding work that a branch actually does.

Idempotent: a solution that already carries applicability rows is never
overwritten, so an ops-set restriction survives a re-run.
"""

import frappe

from gofix.catalogue_sync import resolve_solution

# solution_code -> device CH Categories it can be performed on.
# Codes, not labels: the label is a display string, the code is the identity.
APPLICABILITY = {
	# Screen work — a watch/laptop panel is a different part but the same repair.
	"SCR-TGL": ["Smart Phones", "Feature Phones", "Tablets", "Watches"],
	# Ports and radios that simply do not exist on a watch.
	"CHG-REP": ["Smart Phones", "Feature Phones", "Laptops", "Tablets"],
	"NET-ANT": ["Smart Phones", "Feature Phones", "Tablets", "Laptops"],
	"NET-WBT": ["Smart Phones", "Laptops", "Tablets"],
	# Audio.
	"AUD-SPK": ["Smart Phones", "Feature Phones", "Laptops", "Tablets"],
	"AUD-MIC": ["Smart Phones", "Feature Phones", "Laptops", "Tablets"],
	# Optics.
	"CAM-REP": ["Smart Phones", "Tablets", "Laptops"],
	"CAM-GLS": ["Smart Phones", "Tablets"],
	# Sensors.
	"SNS-FPR": ["Smart Phones", "Laptops", "Tablets"],
	"SNS-DIA": ["Smart Phones", "Tablets"],
	# Software — needs a general-purpose OS.
	"SFT-OSR": ["Smart Phones", "Laptops", "Tablets"],
	"SFT-FRP": ["Smart Phones", "Laptops", "Tablets"],
	"SFT-VIR": ["Smart Phones", "Laptops", "Tablets"],
	# Physical — the four that made the picker wrong.
	"PHY-BCK": ["Smart Phones", "Feature Phones", "Tablets"],
	"PHY-HNG": ["Laptops"],
	"PHY-STR": ["Watches"],
	"BRD-THM": ["Laptops", "Gaming Consoles"],
	# Input hardware unique to a laptop.
	"BTN-KBD": ["Laptops"],
	"BTN-TPD": ["Laptops"],
	# Accessories.
	"ACC-STY": ["Tablets", "Smart Phones"],
}


def execute():
	if not frappe.db.table_exists("GoFix Solution Applicability"):
		return
	if not frappe.get_meta("Repair Solution").get_field("applies_to"):
		return

	seeded, skipped_existing, missing_category = 0, 0, set()

	for code, categories in APPLICABILITY.items():
		name = resolve_solution(code)
		if not name:
			continue

		if frappe.db.exists(
			"GoFix Solution Applicability",
			{"parent": name, "parenttype": "Repair Solution"},
		):
			# Already declared — by an earlier run or by ops. Leave it alone.
			skipped_existing += 1
			continue

		valid = []
		for category in categories:
			if frappe.db.exists("CH Category", category):
				valid.append(category)
			else:
				missing_category.add(category)
		if not valid:
			# Every target category is absent from this environment. Writing no
			# rows leaves the solution universal, which is right: better offered
			# everywhere than offered nowhere.
			continue

		doc = frappe.get_doc("Repair Solution", name)
		for category in valid:
			doc.append("applies_to", {"device_category": category})
		doc.flags.ignore_permissions = True
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)
		seeded += 1

	frappe.db.commit()
	frappe.logger("gofix").info(
		f"GoFix: seeded device applicability on {seeded} repair solution(s); "
		f"{skipped_existing} already declared"
		+ (f"; categories absent here: {sorted(missing_category)}" if missing_category else "")
	)
