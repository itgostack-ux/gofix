"""Provision ERPNext quarantine warehouses required by the repair workflow."""

import frappe

from gofix.setup.warehouse_setup import ensure_quarantine_warehouses


def execute():
	if not all(
		frappe.db.has_column("Company", fieldname)
		for fieldname in ("supplier_return_warehouse", "damaged_stock_warehouse")
	):
		return
	for company in frappe.get_all("Company", filters={"is_group": 0}, pluck="name"):
		ensure_quarantine_warehouses(company)
