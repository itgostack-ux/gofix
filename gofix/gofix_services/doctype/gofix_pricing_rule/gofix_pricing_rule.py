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


def get_pricing_rule(issue_category=None, repair_solution=None, brand=None,
                     item_group=None, warranty_status=None, company=None):
	"""Return the best-matching pricing rule (most specific wins via priority_order)."""
	filters = {"is_active": 1}
	if company:
		filters["company"] = ["in", [company, "", None]]

	rules = frappe.get_all(
		"GoFix Pricing Rule",
		filters=filters,
		fields=[
			"name", "issue_category", "repair_solution", "device_brand",
			"device_item_group", "warranty_status", "labor_rate", "labor_rate_type",
			"min_charge", "max_charge", "spare_markup_percent", "include_spare_cost",
			"warranty_labor_rate", "warranty_deductible_override", "warranty_spare_covered",
			"priority_order",
		],
		order_by="priority_order asc",
	)

	best = None
	best_score = -1

	for rule in rules:
		score = 0
		# Exact match scoring — more specific filters = higher score
		if rule.repair_solution:
			if rule.repair_solution != repair_solution:
				continue
			score += 8
		if rule.issue_category:
			if rule.issue_category != issue_category:
				continue
			score += 4
		if rule.device_brand:
			if rule.device_brand != brand:
				continue
			score += 2
		if rule.device_item_group:
			if rule.device_item_group != item_group:
				continue
			score += 2
		if rule.warranty_status:
			if rule.warranty_status != warranty_status:
				continue
			score += 1

		if score > best_score:
			best_score = score
			best = rule

	return best


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
                                  device_item=None):
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

	labor_total = 0
	spare_total = 0
	line_details = []

	for sol in solutions:
		sol_name = sol.get("repair_solution") or sol.get("name")
		sol_issue = sol.get("issue_category")

		rule = get_pricing_rule(
			issue_category=sol_issue,
			repair_solution=sol_name,
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

	return {
		"labor_total": labor_total,
		"spare_total": spare_total,
		"estimate_total": labor_total + spare_total,
		"line_details": line_details,
		"is_inhouse": 1 if is_inhouse_plan else 0,
	}
