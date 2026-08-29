"""Consolidate repair planning/execution under Service Request.

Service Request is the aggregate root. SR Spare Line records the plan,
Spare Parts Usage records execution, and submitted Stock Entry remains the
inventory authority. The legacy Service Request Spare Part projection and
duplicate Service Request lifecycle/stock columns are retired here.
"""

import frappe
from frappe.utils import flt, now


LEGACY_SPU_COLUMNS = (
	"service_request_name",
	"imei_serial",
	"pur_exempted_value",
	"pur_taxable_value",
	"rog_id",
	"pur_id",
	"pur_line_seq_no",
)


def execute():
	_migrate_legacy_spare_rows()
	_bind_existing_usage_rows()
	_retire_legacy_spare_doctype()
	_drop_columns("Service Request", ("status", "stock_entry"))
	_drop_columns("Spare Parts Usage", LEGACY_SPU_COLUMNS)
	_add_integrity_indexes()
	frappe.clear_cache(doctype="Service Request")
	frappe.clear_cache(doctype="Spare Parts Usage")


def _migrate_legacy_spare_rows():
	if not frappe.db.table_exists("Service Request Spare Part"):
		return
	rows = frappe.db.sql(
		"""
		SELECT name, parent, idx, spare_part_item, item_name, qty, uom, rate,
		       amount, spu_reference, creation, modified, owner, modified_by
		  FROM `tabService Request Spare Part`
		 WHERE parenttype = 'Service Request' AND parentfield = 'spare_parts'
		 ORDER BY parent, idx
		""",
		as_dict=True,
	)
	for row in rows:
		if not frappe.db.exists("Service Request", row.parent):
			continue
		plan_name = _matching_plan_row(row)
		if not plan_name:
			plan_name = frappe.generate_hash(length=10)
			frappe.db.sql(
				"""
				INSERT INTO `tabSR Spare Line`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 parent, parentfield, parenttype, spare_item, item_name, qty, uom,
				 rate, amount, status, spare_usage)
				VALUES (%s, %s, %s, %s, %s, 0, %s, %s, 'spare_lines',
				        'Service Request', %s, %s, %s, %s, %s, %s, %s, %s)
				""",
				(
					plan_name,
					row.creation or now(),
					row.modified or now(),
					row.modified_by or "Administrator",
					row.owner or "Administrator",
					row.idx or 0,
					row.parent,
					row.spare_part_item,
					row.item_name,
					flt(row.qty),
					row.uom,
					flt(row.rate),
					flt(row.amount),
					"Consumed" if row.spu_reference else "Pending",
					row.spu_reference or None,
				),
			)
		if row.spu_reference and frappe.db.exists("Spare Parts Usage", row.spu_reference):
			frappe.db.set_value(
				"Spare Parts Usage",
				row.spu_reference,
				"service_request_spare_line",
				plan_name,
				update_modified=False,
			)


def _matching_plan_row(row):
	return frappe.db.get_value(
		"SR Spare Line",
		{
			"parent": row.parent,
			"parenttype": "Service Request",
			"parentfield": "spare_lines",
			"spare_item": row.spare_part_item,
			"qty": flt(row.qty),
		},
		"name",
	)


def _bind_existing_usage_rows():
	if not frappe.db.table_exists("Spare Parts Usage"):
		return
	rows = frappe.db.sql(
		"""
		SELECT spu.name, spu.creation, spu.modified, spu.owner, spu.modified_by,
		       spu.service_request, spu.spare_part_item, spu.item_name, spu.qty_used,
		       spu.uom, spu.sales_price, spu.part_status,
		       spu.service_request_spare_line, spu.warehouse,
		       sr.source_warehouse, sr.current_location, sr.transferred_to_store,
		       sr.transfer_status
		  FROM `tabSpare Parts Usage` spu
		  JOIN `tabService Request` sr ON sr.name = spu.service_request
		""",
		as_dict=True,
	)
	for row in rows:
		plan_name = row.service_request_spare_line
		if not plan_name or not frappe.db.exists("SR Spare Line", plan_name):
			plan_name = frappe.db.get_value(
				"SR Spare Line",
				{
					"parent": row.service_request,
					"parentfield": "spare_lines",
					"spare_item": row.spare_part_item,
				},
				"name",
			)
		if not plan_name:
			plan_name = frappe.generate_hash(length=10)
			last_idx = frappe.db.sql(
				"SELECT COALESCE(MAX(idx), 0) FROM `tabSR Spare Line` WHERE parent = %s",
				row.service_request,
			)[0][0]
			frappe.db.sql(
				"""
				INSERT INTO `tabSR Spare Line`
				(name, creation, modified, modified_by, owner, docstatus, idx,
				 parent, parentfield, parenttype, spare_item, item_name, qty, uom,
				 rate, amount, status, spare_usage)
				VALUES (%s, %s, %s, %s, %s, 0, %s, %s, 'spare_lines',
				        'Service Request', %s, %s, %s, %s, %s, %s, %s, %s)
				""",
				(
					plan_name, row.creation or now(), row.modified or now(),
					row.modified_by or "Administrator", row.owner or "Administrator",
					last_idx + 1, row.service_request, row.spare_part_item, row.item_name,
					flt(row.qty_used), row.uom, flt(row.sales_price),
					flt(row.qty_used) * flt(row.sales_price), row.part_status or "Pending", row.name,
				),
			)
		if plan_name:
			frappe.db.set_value(
				"Spare Parts Usage",
				row.name,
				"service_request_spare_line",
				plan_name,
				update_modified=False,
			)
			frappe.db.set_value(
				"SR Spare Line",
				plan_name,
				"spare_usage",
				row.name,
				update_modified=False,
			)
		warehouse = row.current_location or row.source_warehouse
		if row.transfer_status in ("In Transit", "Received at Service Center") and row.transferred_to_store:
			warehouse = row.transferred_to_store
		if not row.warehouse and warehouse:
			frappe.db.set_value("Spare Parts Usage", row.name, "warehouse", warehouse, update_modified=False)


def _retire_legacy_spare_doctype():
	if frappe.db.exists("DocType", "Service Request Spare Part"):
		frappe.delete_doc("DocType", "Service Request Spare Part", force=True, ignore_permissions=True)
	if frappe.db.table_exists("Service Request Spare Part"):
		frappe.db.sql_ddl("DROP TABLE `tabService Request Spare Part`")


def _drop_columns(doctype, columns):
	table = f"tab{doctype}"
	if not frappe.db.table_exists(doctype):
		return
	for column in columns:
		if frappe.db.has_column(doctype, column):
			frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{column}`")


def _add_integrity_indexes():
	if frappe.db.table_exists("Spare Parts Usage"):
		frappe.db.add_unique(
			"Spare Parts Usage",
			["service_request_spare_line"],
			constraint_name="uniq_spu_plan_line",
		)
	if frappe.db.table_exists("GoFix Token"):
		frappe.db.add_unique(
			"GoFix Token",
			["service_request"],
			constraint_name="uniq_gofix_token_service_request",
		)
