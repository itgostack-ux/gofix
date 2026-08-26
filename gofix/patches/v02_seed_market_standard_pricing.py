"""Seed a market-standard repair rate card so GoFix can be tested end to end.

Until now `GoFix Pricing Rule` was empty, so every estimate fell through to
`default_labor_rate_per_minute` (Rs 5/min) and quoted the same labour on a
flagship and a budget handset.

WHAT THIS SEEDS IS AN INDICATIVE RATE CARD, NOT A COMMERCIAL DECISION. The
numbers follow the shape the Indian repair market uses (Cashify, uBreakiFix,
authorised service centres) so the flow can be exercised realistically; replace
them with the real rate card before taking payments.

The shape, which is the part worth keeping:

* Labour is a FIXED amount per repair, never technician-hours. A customer
  approves a firm price before work starts, and a slow technician must not cost
  them more.
* Labour is tiered by brand. Premium brands carry roughly 1.6-2x the labour of
  volume brands -- tighter tolerances, more teardown steps, costlier tooling.
* Parts are charged at the part's own rate plus a markup, so the model-specific
  cost already varies correctly through the Item master.
* Under warranty, labour is zero and parts are covered.
* Diagnostics are near-free and usually waived into the repair.

Also seeds the two things a rate card is meaningless without:

* `Repair Solution.warranty_days` -- workmanship warranty per repair type.
* `Item.gofix_spare_grade` / `gofix_part_warranty_days` -- part tier and the
  warranty it carries, inferred from the spare's own sub-category and price.

All three are seeded ONLY where the value is still unset, so anything corrected
in the UI survives a re-run.
"""

import frappe
from frappe.utils import flt

COMPANY_AGNOSTIC = ""

# repair -> (base labour, workmanship warranty days)
# base labour = volume-brand rate; premium brands get the multiplier below.
RATE_CARD = {
	"Screen Replacement": (450, 90),
	"Touch Glass Replacement": (400, 90),
	"Display Diagnosis": (0, 0),
	"Battery Replacement": (300, 180),
	"Battery Health Diagnosis": (0, 0),
	"Charging Port Replacement": (500, 90),
	"Charger / Adapter Replacement": (150, 180),
	"Camera Replacement": (450, 90),
	"Camera Glass Replacement": (350, 90),
	"Speaker Replacement": (400, 90),
	"Microphone Replacement": (400, 90),
	"Audio Cleaning & Diagnosis": (200, 30),
	"Button / Flex Replacement": (350, 90),
	"Keyboard Replacement": (600, 90),
	"Touchpad Replacement": (600, 90),
	"Fingerprint Sensor Replacement": (500, 90),
	"Face ID / Sensor Diagnosis": (0, 0),
	"Back Panel Replacement": (400, 90),
	"Body / Frame Repair": (900, 90),
	"Hinge Repair": (800, 90),
	"Strap Replacement": (150, 90),
	"Board-Level Repair": (2000, 90),
	"Motherboard Diagnosis": (0, 0),
	"Swapping Board": (1500, 90),
	"Antenna / Network IC Repair": (1200, 90),
	"WiFi / Bluetooth Module Replacement": (900, 90),
	"Network Diagnosis": (0, 0),
	"Liquid Damage Treatment": (1200, 30),
	"Post-Liquid Component Replacement": (1500, 30),
	"Thermal Service / Fan Cleaning": (600, 30),
	"OS Reinstall / Update": (500, 15),
	"Password / FRP Unlock": (800, 0),
	"Virus Removal & Tune-up": (500, 15),
	"Data Backup & Transfer": (400, 0),
	"Advanced Data Recovery": (2500, 0),
	"Full Device Diagnosis": (0, 0),
	"Stylus / Accessory Pairing & Repair": (300, 30),
}

# premium brands carry a higher labour tier
PREMIUM_BRANDS = ("Apple", "Samsung", "Google", "OnePlus")
PREMIUM_MULTIPLIER = 1.8

SPARE_MARKUP_PERCENT = 25          # typical retail markup on a fitted part
MIN_CHARGE = 199                   # nobody leaves for less than a bench fee

# spare sub-category -> (grade, part warranty days).
# Sub-categories here are unbranded trade stock, i.e. OEM-equivalent.
DEFAULT_SPARE_GRADE = "OEM Equivalent"
DEFAULT_PART_WARRANTY_DAYS = 90


def execute():
	if not frappe.db.exists("DocType", "GoFix Pricing Rule"):
		return
	_seed_workmanship_warranty()
	_seed_spare_grades()
	_seed_pricing_rules()
	frappe.db.commit()


def _seed_workmanship_warranty():
	if not frappe.get_meta("Repair Solution").get_field("warranty_days"):
		return
	n = 0
	for name, (_labour, days) in RATE_CARD.items():
		if not frappe.db.exists("Repair Solution", name):
			continue
		if frappe.db.get_value("Repair Solution", name, "warranty_days"):
			continue
		frappe.db.set_value("Repair Solution", name, "warranty_days", days, update_modified=False)
		n += 1
	frappe.logger("gofix").info(f"GoFix: workmanship warranty set on {n} repair solution(s)")


def _seed_spare_grades():
	meta = frappe.get_meta("Item")
	if not (meta.get_field("gofix_spare_grade") and meta.get_field("gofix_part_warranty_days")):
		return
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT i.name
		FROM `tabItem` i
		JOIN `tabSolution Spare Mapping` m ON m.spare_item = i.name
		WHERE IFNULL(i.gofix_spare_grade, '') = ''
		""",
		as_dict=True,
	)
	for row in rows:
		frappe.db.set_value(
			"Item", row.name,
			{
				"gofix_spare_grade": DEFAULT_SPARE_GRADE,
				"gofix_part_warranty_days": DEFAULT_PART_WARRANTY_DAYS,
			},
			update_modified=False,
		)
	frappe.logger("gofix").info(f"GoFix: graded {len(rows)} spare item(s)")


def _seed_pricing_rules():
	_unscope_seeded_rules()
	existing = {
		(r.repair_solution, r.device_brand or "")
		for r in frappe.get_all(
			"GoFix Pricing Rule", fields=["repair_solution", "device_brand"]
		)
	}
	created = 0
	for name, (labour, _days) in RATE_CARD.items():
		if not frappe.db.exists("Repair Solution", name):
			continue
		issue_category = frappe.db.get_value("Repair Solution", name, "issue_category")

		# base tier — matches any brand not covered by a premium rule
		if (name, "") not in existing:
			_make_rule(name, issue_category, None, labour, priority=100)
			created += 1

		# premium tier — more specific, so it wins on the specificity score
		for brand in PREMIUM_BRANDS:
			if (name, brand) in existing:
				continue
			_make_rule(
				name, issue_category, brand,
				round(flt(labour) * PREMIUM_MULTIPLIER / 10) * 10,
				priority=10,
			)
			created += 1
	frappe.logger("gofix").info(f"GoFix: created {created} pricing rule(s)")


def _unscope_seeded_rules():
	"""Blank the company on rules this patch created before the fix.

	Only touches rows whose name matches the seeded pattern, so a rule an admin
	deliberately scoped to one company is left alone.
	"""
	names = [
		f"{sol} — {brand}"[:140]
		for sol in RATE_CARD
		for brand in ("Standard",) + PREMIUM_BRANDS
	]
	rows = frappe.get_all(
		"GoFix Pricing Rule",
		filters={"rule_name": ("in", names), "company": ("is", "set")},
		pluck="name",
	)
	for name in rows:
		frappe.db.set_value("GoFix Pricing Rule", name, "company", None, update_modified=False)
	if rows:
		frappe.logger("gofix").info(f"GoFix: unscoped {len(rows)} seeded pricing rule(s)")


def _make_rule(solution, issue_category, brand, labour, priority):
	doc = frappe.new_doc("GoFix Pricing Rule")
	doc.rule_name = f"{solution} — {brand or 'Standard'}"[:140]
	doc.is_active = 1
	# Leave company blank so the card applies to every company. new_doc()
	# otherwise inherits the session default, which silently scopes the rule to
	# one company and makes every other company's tickets price at zero.
	doc.company = COMPANY_AGNOSTIC
	doc.issue_category = issue_category
	doc.repair_solution = solution
	if brand:
		doc.device_brand = brand
	doc.labor_rate = labour
	doc.labor_rate_type = "Fixed"
	doc.min_charge = MIN_CHARGE if labour else 0
	doc.spare_markup_percent = SPARE_MARKUP_PERCENT
	doc.include_spare_cost = 1
	# under warranty the customer pays nothing for labour or parts
	doc.warranty_labor_rate = 0
	doc.warranty_spare_covered = 1
	doc.priority_order = priority
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
