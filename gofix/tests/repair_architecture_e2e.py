"""Rollback-safe live-site proof for the consolidated repair spare flow."""

import frappe
from frappe.utils import add_days, flt, now_datetime, today


def run():
	proof = {"checks": []}
	savepoint = "repair_architecture_e2e"
	frappe.db.savepoint(savepoint)
	original_user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		seed_sr = frappe.get_all(
			"Service Request",
			filters={"docstatus": 0},
			fields=["name", "company"],
			limit_page_length=1,
		)
		if not seed_sr:
			frappe.throw("A draft Service Request is required for the rollback-safe test.")
		seed_sr = frappe.get_doc("Service Request", seed_sr[0].name)
		profile = frappe.db.get_value(
			"POS Profile",
			{"company": seed_sr.company, "disabled": 0, "warehouse": ("is", "set")},
			"name",
		)
		if not profile:
			frappe.throw("An active POS Profile is required for the direct-intake test.")
		from ch_pos.api.repair import create_service_intake_from_pos

		intake = create_service_intake_from_pos(
			{
				"customer": seed_sr.customer,
				"contact_number": seed_sr.contact_number or "9876543210",
				"device_item": seed_sr.device_item,
				"issue_category": seed_sr.issue_category or "Battery",
				"issue_description": "Rollback-safe POS repair intake architecture proof",
				"device_condition": "Good",
				"accessories_received": "Device only",
				"data_backup_disclaimer": 1,
				"mode_of_service": "Walk-in",
				"priority": "Medium",
			},
			pos_profile=profile,
		)
		pos_sr = frappe.get_doc("Service Request", intake["name"])
		_assert(pos_sr.docstatus == 1 and pos_sr.walkin_source == "POS Counter",
			"POS creates the canonical submitted Service Request directly", proof)
		_assert(not frappe.db.exists("DocType", "POS Repair Intake"),
			"Duplicate POS Repair Intake DocType is absent", proof)
		from gofix.tracking import _get_by_token, ensure_tracking_token

		tracking_token = ensure_tracking_token(pos_sr.name)
		tracked = _get_by_token(tracking_token)
		_assert(tracked and tracked.get("name") == pos_sr.name,
			"Customer tracking resolves through canonical decision state", proof)
		pos_sr.db_set("decision", "Accepted", update_modified=True)
		from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
			_log_ops_stage,
			_mark_sr_in_service,
		)
		_mark_sr_in_service(pos_sr.name)
		_log_ops_stage(pos_sr.name, "analysis", "confirm")
		logs = frappe.get_all(
			"GoFix Status Log",
			filters={"parent": pos_sr.name, "parenttype": "Service Request"},
			fields=["event_type", "from_status", "to_status"],
			order_by="idx",
		)
		_assert(
			len([row for row in logs if row.event_type == "Lifecycle"]) == 2,
			"Lifecycle transitions are persisted in the canonical status log",
			proof,
		)
		_assert(
			len([row for row in logs if row.event_type == "Operations Stage"]) == 1,
			"Operational stages are explicitly distinguished from lifecycle state",
			proof,
		)
		from gofix.gofix_services.api import get_store_service_board

		board = get_store_service_board(pos_sr.source_warehouse, tab="in_progress")
		board_row = next((row for row in board["rows"] if row.name == pos_sr.name), None)
		_assert(board_row and board_row.status == "In Service",
			"Store service board queries the canonical decision column", proof)
		sr = pos_sr

		serial = frappe.db.sql(
			"""
			SELECT sn.name, sn.item_code, sn.warehouse, i.stock_uom, i.item_name,
			       b.actual_qty
			  FROM `tabSerial No` sn
			  JOIN `tabItem` i ON i.name = sn.item_code
			  JOIN `tabWarehouse` w ON w.name = sn.warehouse
			  JOIN `tabBin` b ON b.item_code = sn.item_code AND b.warehouse = sn.warehouse
			 WHERE sn.status = 'Active' AND w.company = %s AND b.actual_qty > 0
			 ORDER BY b.actual_qty DESC, sn.name
			 LIMIT 1
			""",
			sr.company,
			as_dict=True,
		)
		if not serial:
			frappe.throw("No available serialized stock exists for the repair architecture test.")
		serial = serial[0]
		before_qty = flt(serial.actual_qty)

		# All mutations after this point are reverted to the savepoint.
		frappe.db.set_value("Item", serial.item_code, "gofix_universal_spare", 1, update_modified=False)
		frappe.db.set_value(
			"Service Request",
			sr.name,
			{
				"source_warehouse": serial.warehouse,
				"current_location": serial.warehouse,
				"transferred_to_store": None,
				"transfer_status": "",
			},
			update_modified=False,
		)
		sr.reload()
		from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
			add_spare_to_ticket,
			update_spare_genealogy,
		)

		reservation = add_spare_to_ticket(
			sr.name,
			serial.item_code,
			1,
			rate=1,
			installed_part_serial=serial.name,
		)
		plan_name = reservation["spare_line"]
		usage = frappe.get_doc("Spare Parts Usage", reservation["spare_usage"])
		reserved_qty = flt(frappe.db.get_value(
			"Bin", {"item_code": serial.item_code, "warehouse": serial.warehouse}, "actual_qty"
		))
		_assert(
			usage.docstatus == 0 and usage.part_status == "Reserved" and not usage.stock_entry,
			"Planning creates a draft reservation without issuing stock",
			proof,
		)
		_assert(reserved_qty == before_qty, "Reservation leaves standard Bin unchanged", proof)
		direct_state_change_blocked = False
		try:
			usage.part_status = "Returned"
			usage.save(ignore_permissions=True)
		except frappe.PermissionError:
			direct_state_change_blocked = True
		_assert(
			direct_state_change_blocked,
			"Direct document saves cannot bypass authorised spare lifecycle actions",
			proof,
		)
		usage.reload()
		if usage.requires_approval:
			usage._authorize_approval_transition()
			usage.approval_status = "Approved"
			usage.approved_by = frappe.session.user
			usage.approval_datetime = now_datetime()
			usage.approval_remarks = "Automated rollback-safe architecture test"
			usage.save(ignore_permissions=True)
		consumption = update_spare_genealogy(
			sr.name,
			plan_name,
			installed_part_serial=serial.name,
			consume=1,
		)
		usage = frappe.get_doc("Spare Parts Usage", consumption["spare_usage"])

		stock_entry = frappe.get_doc("Stock Entry", usage.stock_entry)
		_assert(stock_entry.docstatus == 1, "Spare usage created a submitted Stock Entry", proof)
		_assert(stock_entry.purpose == "Material Issue", "Stock authority is a Material Issue", proof)
		_assert(
			any(row.item_code == serial.item_code and row.s_warehouse == serial.warehouse for row in stock_entry.items),
			"Stock Entry item and warehouse match the execution record",
			proof,
		)
		sle_qty = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(actual_qty), 0)
			  FROM `tabStock Ledger Entry`
			 WHERE voucher_type = 'Stock Entry' AND voucher_no = %s
			   AND item_code = %s AND warehouse = %s AND is_cancelled = 0
			""",
			(stock_entry.name, serial.item_code, serial.warehouse),
		)[0][0]
		_assert(flt(sle_qty) == -1, "Standard Stock Ledger records the single issue", proof)
		gl_count = frappe.db.count(
			"GL Entry",
			{"voucher_type": "Stock Entry", "voucher_no": stock_entry.name, "is_cancelled": 0},
		)
		_assert(gl_count >= 2, "Standard GL records the inventory expense posting", proof)
		plan_state = frappe.db.get_value("SR Spare Line", plan_name, ["status", "spare_usage"], as_dict=True)
		_assert(plan_state.status == "Consumed" and plan_state.spare_usage == usage.name,
			"Planning row projects the executed usage", proof)
		after_qty = flt(frappe.db.get_value("Bin", {"item_code": serial.item_code, "warehouse": serial.warehouse}, "actual_qty"))
		_assert(abs(after_qty - (before_qty - 1)) < 0.0001, "Standard Bin decreased exactly once", proof)

		from ch_pos.api.pos_api import _normalize_repair_spare_parts

		billable = _normalize_repair_spare_parts(
			sr,
			[{"spare_usage": usage.name, "spare_part_item": serial.item_code, "qty": 1, "rate": 1}],
		)
		_assert(len(billable) == 1, "POS billing reads submitted Spare Parts Usage", proof)
		underbilling_blocked = False
		try:
			_normalize_repair_spare_parts(sr, [])
		except Exception:
			underbilling_blocked = True
		_assert(underbilling_blocked, "POS cannot omit consumed spares from billing", proof)
		tampering_blocked = False
		try:
			_normalize_repair_spare_parts(
				sr,
				[{"spare_usage": usage.name, "spare_part_item": serial.item_code, "qty": 0.5, "rate": 1}],
			)
		except Exception:
			tampering_blocked = True
		_assert(tampering_blocked, "POS cannot alter consumed quantity during billing", proof)

		duplicate_blocked = False
		try:
			duplicate = frappe.copy_doc(usage)
			duplicate.name = None
			duplicate.docstatus = 0
			duplicate.stock_entry = None
			duplicate.part_status = "Reserved"
			duplicate.insert(ignore_permissions=True)
		except Exception:
			duplicate_blocked = True
		_assert(duplicate_blocked, "A plan line cannot be consumed twice", proof)

		# Defective stock is physically segregated by a standard Material Transfer;
		# it cannot be relabelled while remaining in a saleable store Bin.
		defective_serial = frappe.db.get_value(
			"Serial No",
			{
				"item_code": serial.item_code,
				"warehouse": serial.warehouse,
				"status": "Active",
				"name": ("!=", serial.name),
			},
			"name",
		)
		if not defective_serial:
			frappe.throw("A second active Serial No is required for the defective-stock proof.")
		damaged_warehouse = frappe.db.get_value("Company", sr.company, "damaged_stock_warehouse")
		damaged_before = flt(frappe.db.get_value(
			"Bin", {"item_code": serial.item_code, "warehouse": damaged_warehouse}, "actual_qty"
		))
		defective_reservation = add_spare_to_ticket(
			sr.name,
			serial.item_code,
			1,
			rate=1,
			installed_part_serial=defective_serial,
		)
		from gofix.gofix_services.doctype.spare_parts_usage.spare_parts_usage import mark_defective

		mark_defective(
			defective_reservation["spare_usage"],
			"Technician Damage",
			description="Rollback-safe defective-stock segregation proof",
			action="Dispose",
		)
		defective_usage = frappe.get_doc("Spare Parts Usage", defective_reservation["spare_usage"])
		defective_transfer = frappe.get_doc("Stock Entry", defective_usage.defective_stock_entry)
		_assert(
			defective_transfer.docstatus == 1 and defective_transfer.purpose == "Material Transfer",
			"Defective spare creates a submitted Material Transfer",
			proof,
		)
		_assert(
			any(
				row.item_code == serial.item_code
				and row.s_warehouse == serial.warehouse
				and row.t_warehouse == damaged_warehouse
				for row in defective_transfer.items
			),
			"Defective spare moves from saleable stock to the configured damaged warehouse",
			proof,
		)
		damaged_after = flt(frappe.db.get_value(
			"Bin", {"item_code": serial.item_code, "warehouse": damaged_warehouse}, "actual_qty"
		))
		_assert(
			abs(damaged_after - (damaged_before + 1)) < 0.0001,
			"Standard Bin records defective quarantine exactly once",
			proof,
		)

		from gofix.gofix_services.doctype.service_request.service_request import (
			auto_expire_stale_requests,
		)
		frappe.db.set_value(
			"Service Request",
			seed_sr.name,
			{"decision": "Draft", "service_order": None, "creation": add_days(today(), -2)},
			update_modified=False,
		)
		expiry = auto_expire_stale_requests(days_threshold=1)
		seed_decision = frappe.db.get_value("Service Request", seed_sr.name, "decision")
		_assert(expiry["expired"] >= 1 and seed_decision == "Expired",
			"Stale repair expiry uses the canonical decision column", proof)

		proof.update(
			{
				"ok": True,
				"service_request": sr.name,
				"spare_usage": usage.name,
				"stock_entry": stock_entry.name,
				"item_code": serial.item_code,
				"warehouse": serial.warehouse,
				"before_qty": before_qty,
				"after_qty_during_test": after_qty,
				"rolled_back": True,
			}
		)
		return proof
	finally:
		frappe.db.rollback(save_point=savepoint)
		frappe.set_user(original_user)


def _assert(condition, label, proof):
	if not condition:
		frappe.throw(f"Repair architecture E2E failed: {label}")
	proof["checks"].append(label)
