# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class GoFixPricingRule(Document):
	def validate(self):
		if self.max_charge and self.min_charge and flt(self.max_charge) < flt(self.min_charge):
			frappe.throw(_("Maximum Charge cannot be less than Minimum Charge"), title=_("Gofix Pricing Rule Error"))


# How specific each dimension is. A rule that names a dimension only matches
# when that dimension matches; a blank one is a wildcard. The winner is the
# rule with the highest total, so the most specific rule always wins.
#
# The device axis is a ladder, which is what makes market-shaped pricing work:
# a laptop is not a phone, an Apple is not an Android, and an iPhone 15 Pro Max
# is not an iPhone 11. Each level narrows the one above it --
#
#     category (Smart Phones | Laptops)
#       └── sub category (iOS Phones | Android Phones)
#             └── brand (Apple | Samsung | Xiaomi)
#                   └── model (Apple iPhone 15 Pro Max)
#
# so you write the rules you actually have -- one for laptops, one for Apple
# phones, a handful for the models that genuinely cost more -- and everything
# else falls through to the broader rule rather than needing a row per device.
#
# The numbers are spaced so the original five dimensions keep exactly the
# relative order they had (solution > issue > brand = item group > warranty).
# Nothing that matched before matches differently now; the new levels slot in
# between brand and warranty.
_DIMENSION_WEIGHT = {
	"repair_solution": 64,
	"issue_category": 32,
	"device_model": 16,
	"device_brand": 8,
	"device_sub_category": 4,
	"device_category": 2,
	"device_item_group": 2,
	"warranty_status": 1,
}

_RULE_FIELDS = [
	"name", "issue_category", "repair_solution", "device_brand",
	"device_item_group", "device_category", "device_sub_category", "device_model",
	"warranty_status", "labor_rate", "labor_rate_type",
	"min_charge", "max_charge", "spare_markup_percent", "include_spare_cost",
	"warranty_labor_rate", "warranty_deductible_override", "warranty_spare_covered",
	"service_charge", "priority_order",
]


def device_axis(device_item=None, device_model=None, brand=None, category=None,
                sub_category=None) -> dict:
	"""Resolve the four device levels from whatever the caller happens to hold.

	Callers have different things in hand -- the Ops Hub has the Service
	Request's own device fields, triage has an Item, an older caller has only a
	brand string. Rather than make every one of them look the rest up, each
	level is derived from the one below it when it was not supplied. A caller
	that knows nothing still gets a usable axis of all-None, which simply means
	every device rule is a wildcard and pricing behaves exactly as it did
	before this ladder existed.
	"""
	model = device_model
	if not model and device_item:
		model = frappe.db.get_value("Item", device_item, "ch_model")
	if model and not (brand and sub_category):
		row = frappe.db.get_value("CH Model", model, ["brand", "sub_category"], as_dict=True) or {}
		brand = brand or row.get("brand")
		sub_category = sub_category or row.get("sub_category")
	if sub_category and not category:
		category = frappe.db.get_value("CH Sub Category", sub_category, "category")
	return {
		"device_model": model or None,
		"device_brand": brand or None,
		"device_sub_category": sub_category or None,
		"device_category": category or None,
	}


def _active_rules(company=None) -> list:
	filters = {"is_active": 1}
	if company:
		filters["company"] = ["in", [company, "", None]]
	return frappe.get_all(
		"GoFix Pricing Rule", filters=filters, fields=_RULE_FIELDS,
		order_by="priority_order asc",
	)


def _score(rule, wanted: dict):
	"""Specificity of this rule for what we are pricing, or None if it excludes it.

	A dimension the rule names but that does not match is a refusal, not a low
	score: a rule written for laptops must never price a phone just because
	nothing better exists.
	"""
	score = 0
	for dimension, weight in _DIMENSION_WEIGHT.items():
		value = rule.get(dimension)
		if not value:
			continue                      # blank = any
		if value != wanted.get(dimension):
			return None
		score += weight
	return score


def get_pricing_rule(issue_category=None, repair_solution=None, brand=None,
                     item_group=None, warranty_status=None, company=None,
                     device_item=None, device_model=None, device_category=None,
                     device_sub_category=None):
	"""Return the best-matching pricing rule (most specific wins)."""
	wanted = device_axis(
		device_item=device_item, device_model=device_model, brand=brand,
		category=device_category, sub_category=device_sub_category,
	)
	wanted.update({
		"repair_solution": repair_solution,
		"issue_category": issue_category,
		"device_item_group": item_group,
		"warranty_status": warranty_status,
	})

	best, best_score = None, -1
	for rule in _active_rules(company):
		# A rule that sets no labour rate is a bench-fee rule, not a labour
		# rule -- resolve_service_charge is what reads those. Letting one win
		# here would quote ZERO labour for any repair that has no rule of its
		# own, replacing the per-minute fallback with nothing. Mirror image of
		# the solution/issue guard in resolve_service_charge.
		if not flt(rule.get("labor_rate")) and not flt(rule.get("warranty_labor_rate")):
			continue
		score = _score(rule, wanted)
		if score is not None and score > best_score:
			best, best_score = rule, score
	return best


def resolve_service_charge(device_item=None, device_model=None, brand=None,
                           device_category=None, device_sub_category=None,
                           company=None) -> dict:
	"""The bench fee for this device, down the same ladder.

	Scored on the device axis alone. The fee is charged once for handling the
	device -- logging, testing, cleaning, handing it back -- so it cannot be
	resolved per repair solution the way a labour rate is; a ticket with three
	repairs is still one device across one bench.

	Falls back to the GOFIX-SERVICE-CHARGE item price when no rule sets one, so
	a bench that has written no device rules keeps quoting exactly what it
	quoted before.
	"""
	wanted = device_axis(
		device_item=device_item, device_model=device_model, brand=brand,
		category=device_category, sub_category=device_sub_category,
	)
	device_weights = {k: v for k, v in _DIMENSION_WEIGHT.items() if k.startswith("device_")}

	best, best_score = None, -1
	for rule in _active_rules(company):
		if not flt(rule.get("service_charge")):
			continue
		# Only the device axis. A rule that also names a solution or an issue
		# category is about that repair, not about handling this device, so it
		# must not decide the bench fee.
		if rule.get("repair_solution") or rule.get("issue_category"):
			continue
		score = 0
		excluded = False
		for dimension, weight in device_weights.items():
			value = rule.get(dimension)
			if not value:
				continue
			if value != wanted.get(dimension):
				excluded = True
				break
			score += weight
		if excluded or score <= best_score:
			continue
		best, best_score = rule, score

	from gofix.setup.gofix_service_charge_item import get_service_charge

	charge = get_service_charge() or {}
	if best:
		charge = dict(charge)
		charge["rate"] = flt(best.get("service_charge"))
		charge["pricing_rule"] = best.get("name")
	return charge


def _select_spare(solution, device_item=None):
	"""Pick the ONE part to quote for ``solution``, plus how many alternatives fit.

	A repair consumes a single part. "Screen Replacement" maps to hundreds of
	model-specific screens, so the estimate has to choose rather than sum.

	Choice order:
	  1. only parts compatible with the device being repaired, when it is known;
	  2. cheapest first, so the quote is the customer-friendly default and any
	     OEM upgrade is an explicit up-sell rather than a surprise.

	With no device supplied nothing is quoted for parts -- a blind guess across
	models would be worse than an obviously incomplete estimate.
	"""
	rows = frappe.get_all(
		"Solution Spare Mapping",
		filters={"repair_solution": solution, "is_active": 1},
		fields=["spare_item", "default_qty"],
		limit_page_length=0,
	)
	if not rows:
		return None, 0

	if not device_item:
		return None, len(rows)

	from gofix.gofix_services.api import is_spare_compatible_with_device

	fitting = []
	for row in rows:
		if not is_spare_compatible_with_device(row.spare_item, device_item):
			continue
		info = frappe.db.get_value(
			"Item", row.spare_item,
			["standard_rate", "gofix_spare_grade", "disabled"], as_dict=True
		) or frappe._dict()
		if info.get("disabled"):
			continue
		fitting.append({
			"item": row.spare_item,
			"qty": row.default_qty or 1,
			"rate": flt(info.get("standard_rate")),
			"grade": info.get("gofix_spare_grade"),
		})

	if not fitting:
		return None, 0
	fitting.sort(key=lambda r: r["rate"])
	return fitting[0], len(fitting) - 1


def calculate_estimate_from_rules(issue_categories, solutions, brand=None,
                                  item_group=None, warranty_status=None,
                                  company=None, warranty_plan=None,
                                  device_item=None, device_model=None,
                                  device_category=None, device_sub_category=None):
	"""Calculate a full estimate using pricing rules.

	Returns dict with labor_total, spare_total, estimate_total, line_details.

	#20 — In-house warranty plan branch:
	When the supplied ``warranty_plan`` has ``is_inhouse`` enabled, the
	repair is performed in-house under a service-bundle plan and the
	estimate is capped at zero parts/labor — the customer is billed only
	for applicable GST on the plan's nominal service value (which is
	calculated by the caller from the plan's pricing). The line details
	still record the matched pricing rule for audit.
	"""
	# #20 — Resolve in-house flag once. Skip silently when plan can't be
	# resolved so legacy callers without warranty_plan keep working.
	is_inhouse_plan = False
	if warranty_plan:
		is_inhouse_plan = bool(
			frappe.db.get_value("CH Warranty Plan", warranty_plan, "is_inhouse")
		)

	# Resolved once for the whole ticket: the device does not change between
	# repairs, and looking it up per solution would hit the same three rows
	# again for every line.
	axis = device_axis(
		device_item=device_item, device_model=device_model, brand=brand,
		category=device_category, sub_category=device_sub_category,
	)

	labor_total = 0
	spare_total = 0
	line_details = []

	for sol in solutions:
		sol_name = sol.get("repair_solution") or sol.get("name")
		sol_issue = sol.get("issue_category")

		rule = get_pricing_rule(
			issue_category=sol_issue,
			repair_solution=sol_name,
			device_model=axis["device_model"],
			device_category=axis["device_category"],
			device_sub_category=axis["device_sub_category"],
			brand=brand,
			item_group=item_group,
			warranty_status=warranty_status,
			company=company,
		)

		labor = 0
		spare = 0

		# #20 — In-house plan: zero out parts/labor, customer pays GST only.
		if is_inhouse_plan:
			line_details.append({
				"repair_solution": sol_name,
				"issue_category": sol_issue,
				"labor": 0,
				"spare": 0,
				"total": 0,
				"pricing_rule": rule.name if rule else None,
				"inhouse": 1,
			})
			continue

		if rule:
			# Determine labor rate
			is_warranty = warranty_status == "Under Warranty"
			if is_warranty and rule.warranty_labor_rate is not None:
				labor = flt(rule.warranty_labor_rate)
			else:
				labor = flt(rule.labor_rate)

			# A repair consumes ONE part, not every part mapped to it. With
			# hundreds of model-specific spares behind "Screen Replacement",
			# summing the mapping would quote the whole shelf.
			chosen, alternatives = _select_spare(sol_name, device_item)
			if chosen:
				markup = flt(rule.spare_markup_percent) / 100
				sp_cost = flt(chosen["qty"]) * flt(chosen["rate"]) * (1 + markup)
				if is_warranty and rule.warranty_spare_covered:
					sp_cost = 0
				if not rule.include_spare_cost:
					sp_cost = 0
				spare += sp_cost

			# Enforce min/max. A repair fully covered by warranty is exempt --
			# the minimum is a bench fee for paying customers, not a way to
			# charge someone whose labour and parts are both covered.
			fully_covered = is_warranty and not flt(rule.warranty_labor_rate) and rule.warranty_spare_covered
			total_line = labor + spare
			if rule.min_charge and total_line < flt(rule.min_charge) and not fully_covered:
				labor = flt(rule.min_charge) - spare
			if rule.max_charge and total_line > flt(rule.max_charge):
				labor = flt(rule.max_charge) - spare

		labor_total += labor
		spare_total += spare
		line_details.append({
			"repair_solution": sol_name,
			"issue_category": sol_issue,
			"labor": labor,
			"spare": spare,
			"total": labor + spare,
			"pricing_rule": rule.name if rule else None,
			"spare_item": (chosen or {}).get("item") if rule else None,
			"spare_grade": (chosen or {}).get("grade") if rule else None,
			"spare_alternatives": alternatives if rule else 0,
		})

	# The bench fee, last on the estimate. Every repair carries the cost of
	# logging, testing, cleaning and handing back the device, and it belonged to
	# no line -- so small jobs looked free to run and the counter had to explain
	# a total that did not add up from what was listed. Read from the Item's
	# price list, so the amount is owned by whoever owns pricing.
	#
	# Not charged when nothing else is: an in-house plan bills no labour and no
	# parts, and a bench fee on a zero estimate is a bill for nothing.
	if not is_inhouse_plan and line_details:
		charge = resolve_service_charge(
			device_item=device_item,
			device_model=axis["device_model"],
			brand=axis["device_brand"],
			device_category=axis["device_category"],
			device_sub_category=axis["device_sub_category"],
			company=company,
		)
		if charge and flt(charge.get("rate")) > 0:
			labor_total += flt(charge["rate"])
			line_details.append({
				"repair_solution": charge["item_code"],
				"solution_label": charge["item_name"],
				"issue_category": None,
				"labor": flt(charge["rate"]),
				"spare": 0,
				"total": flt(charge["rate"]),
				"pricing_rule": charge.get("pricing_rule"),
				"is_service_charge": 1,
			})

	return {
		"labor_total": labor_total,
		"spare_total": spare_total,
		"estimate_total": labor_total + spare_total,
		"line_details": line_details,
		"is_inhouse": 1 if is_inhouse_plan else 0,
	}
