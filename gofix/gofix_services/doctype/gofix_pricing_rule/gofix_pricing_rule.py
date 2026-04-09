# Copyright (c) 2026, GoFix and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class GoFixPricingRule(Document):
	def validate(self):
		if self.max_charge and self.min_charge and flt(self.max_charge) < flt(self.min_charge):
			frappe.throw(_("Maximum Charge cannot be less than Minimum Charge"))


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


def calculate_estimate_from_rules(issue_categories, solutions, brand=None,
                                  item_group=None, warranty_status=None,
                                  company=None):
	"""Calculate a full estimate using pricing rules.

	Returns dict with labor_total, spare_total, estimate_total, line_details.
	"""
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

		if rule:
			# Determine labor rate
			is_warranty = warranty_status == "Under Warranty"
			if is_warranty and rule.warranty_labor_rate is not None:
				labor = flt(rule.warranty_labor_rate)
			else:
				labor = flt(rule.labor_rate)

			# Spare cost from solution mapping
			spare_items = frappe.get_all(
				"Solution Spare Mapping",
				filters={"repair_solution": sol_name, "is_active": 1},
				fields=["spare_item", "default_qty"],
			)
			for sp in spare_items:
				item_rate = flt(frappe.db.get_value("Item", sp.spare_item, "standard_rate"))
				markup = flt(rule.spare_markup_percent) / 100
				sp_cost = flt(sp.default_qty) * item_rate * (1 + markup)

				if is_warranty and rule.warranty_spare_covered:
					sp_cost = 0
				if not rule.include_spare_cost:
					sp_cost = 0

				spare += sp_cost

			# Enforce min/max
			total_line = labor + spare
			if rule.min_charge and total_line < flt(rule.min_charge):
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
		})

	return {
		"labor_total": labor_total,
		"spare_total": spare_total,
		"estimate_total": labor_total + spare_total,
		"line_details": line_details,
	}
