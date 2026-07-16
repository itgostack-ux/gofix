"""One full GoFix end-to-end demo entry (kept, not cleaned up).

Story: Karthik Raman walks into GF-ASHOKNAGAR with a water-damaged HP Pavilion
laptop. POS raises the Service Request; analysis + estimate + customer
approval; job assigned to an L3 technician; device shipped to Vepery Hub via a
CH Transfer Manifest on a CH Logistics Trip (visible in the logistics control
tower); keyboard spare procured (MR -> PO -> Purchase Receipt) and consumed;
technician logs 5h; QC pass; device shipped back on a reverse manifest/trip;
billed and paid at the Ashok Nagar POS.

Run phase by phase:
    bench --site erpnext.local execute gofix.e2e_water_damage_demo.phase0
    ... through phase8

State (doc names) is passed between phases via STATE_FILE.
"""

import json
import traceback

import frappe
from frappe.utils import add_days, add_to_date, flt, now_datetime, nowdate, today

STATE_FILE = "/tmp/claude-1000/-home-palla-erpnext-bench/78f7ffed-71a4-4dd7-a576-31754719375a/scratchpad/e2e_state.json"

COMPANY = "GOFIX SOLUTIONS PRIVATE LIMITED"
STORE_WH = "GF-ASHOKNAGAR-Sellable - GF"
HUB_WH = "Vepery - Hub-Sellable-01 - GF"
POS_PROFILE = "POS - STO-GSPL-CHENNA-0004"
DEVICE_ITEM = "I08967"  # HP Pavilion Gaming Laptop 15.6"
DEVICE_SERIAL = "5CDE2E0717"
SPARE_ITEM_CODE = "SP-HPPAV15-KBD"
SUPPLIER = "S0001"

# Ashok Nagar / Vepery approximate geocodes (used only if warehouses lack geo)
STORE_GEO = (13.0348, 80.2115)
HUB_GEO = (13.0827, 80.2707)


def _state() -> dict:
	try:
		with open(STATE_FILE) as f:
			return json.load(f)
	except Exception:
		return {}


def _save(state: dict) -> None:
	with open(STATE_FILE, "w") as f:
		json.dump(state, f, indent=1, default=str)


def _step(tag, value=""):
	print(f"[E2E] {tag}: {value}")


# ---------------------------------------------------------------------------
# Phase 0 — masters & config (customer, technician, driver, vehicle, spare)
# ---------------------------------------------------------------------------


def phase0():
	st = _state()

	# Customer ---------------------------------------------------------
	cust = frappe.db.get_value("Customer", {"customer_name": "Karthik Raman"}, "name")
	if not cust:
		c = frappe.new_doc("Customer")
		c.customer_name = "Karthik Raman"
		c.customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		c.territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
		c.mobile_no = "9841027365"
		c.insert(ignore_permissions=True)
		cust = c.name
	st["customer"] = cust
	_step("customer", cust)

	# Technician (L3 lead — water damage needs Advanced skill) ----------
	tech = frappe.db.get_value("Employee", {"employee_name": "Suresh Kumar", "company": COMPANY}, "name")
	if not tech:
		e = frappe.new_doc("Employee")
		e.first_name = "Suresh"
		e.last_name = "Kumar"
		e.gender = "Male"
		e.date_of_birth = "1992-04-18"
		e.date_of_joining = "2024-06-01"
		e.status = "Active"
		e.company = COMPANY
		e.technician_grade = "L3 - Lead Technician"
		if e.meta.has_field("ctc"):
			e.ctc = 832000  # -> 400/hr on the 2080h year used by labour costing
		e.insert(ignore_permissions=True)
		tech = e.name
	st["technician"] = tech
	_step("technician", tech)

	# Driver + Vehicle for the logistics legs ---------------------------
	drv = frappe.db.get_value("Driver", {"full_name": "Murugan D"}, "name")
	if not drv:
		d = frappe.new_doc("Driver")
		d.full_name = "Murugan D"
		d.status = "Active"
		d.cell_number = "9840012345"
		d.insert(ignore_permissions=True)
		drv = d.name
	st["driver"] = drv
	_step("driver", drv)

	veh = frappe.db.get_value("Vehicle", {"license_plate": "TN01AB4321"}, "name")
	if not veh:
		v = frappe.new_doc("Vehicle")
		v.license_plate = "TN01AB4321"
		v.make = "Tata"
		v.model = "Ace"
		v.last_odometer = 42150
		v.fuel_type = "Diesel"
		v.uom = "Litre" if frappe.db.exists("UOM", "Litre") else frappe.db.get_value("UOM", {}, "name")
		v.insert(ignore_permissions=True)
		veh = v.name
	st["vehicle"] = veh
	_step("vehicle", veh)

	# Warehouse geo (only if missing) so geofenced pickup/delivery works -
	for wh, (lat, lng) in ((STORE_WH, STORE_GEO), (HUB_WH, HUB_GEO)):
		if frappe.db.has_column("Warehouse", "custom_latitude"):
			cur = frappe.db.get_value("Warehouse", wh, ["custom_latitude", "custom_longitude"])
			if not (cur and cur[0]):
				frappe.db.set_value(
					"Warehouse", wh, {"custom_latitude": lat, "custom_longitude": lng}, update_modified=False
				)
				_step("geo backfilled", wh)

	# Company hub config -------------------------------------------------
	if not frappe.db.get_value("Company", COMPANY, "master_hub_warehouse"):
		frappe.db.set_value("Company", COMPANY, "master_hub_warehouse", HUB_WH, update_modified=False)
		_step("master_hub_warehouse", HUB_WH)

	# Spare item: HP Pavilion 15 keyboard assembly -----------------------
	if not frappe.db.exists("Item", SPARE_ITEM_CODE):
		subcat = "Laptop Spares-Keyboards"
		if not frappe.db.exists("CH Sub Category", subcat):
			sc = frappe.new_doc("CH Sub Category")
			sc.category = "Laptop Spares"
			sc.sub_category_name = "Keyboards"
			sc.item_group = "Spares"
			sc.prefix = "LSK"
			if sc.meta.has_field("item_nature"):
				sc.item_nature = "Simple Auto-Named"
			if sc.meta.has_field("gst_hsn_code"):
				sc.gst_hsn_code = "84713010"
			sc.insert(ignore_permissions=True)
			subcat = sc.name
		it = frappe.new_doc("Item")
		it.item_code = SPARE_ITEM_CODE
		it.item_name = "HP Pavilion 15 Keyboard Assembly"
		it.description = "Replacement backlit keyboard assembly, HP Pavilion 15 (water-damage repair spare)"
		it.item_group = "Spares"
		it.stock_uom = "Nos"
		it.is_stock_item = 1
		it.is_purchase_item = 1
		it.is_sales_item = 1
		it.brand = "HP"
		it.ch_category = "Laptop Spares"
		it.ch_sub_category = subcat
		it.gst_hsn_code = frappe.db.get_value("Item", DEVICE_ITEM, "gst_hsn_code") or "84713010"
		it.standard_rate = 2500
		it.valuation_rate = 1800
		if it.meta.has_field("ch_item_mrp"):
			it.ch_item_mrp = 2999
		it.append("supplier_items", {"supplier": SUPPLIER})
		it.insert(ignore_permissions=True)
		if it.meta.has_field("ch_lifecycle_status") and it.ch_lifecycle_status != "Active":
			frappe.db.set_value("Item", it.name, "ch_lifecycle_status", "Active", update_modified=False)
	st["spare_item"] = SPARE_ITEM_CODE
	_step("spare item", SPARE_ITEM_CODE)

	if not frappe.db.exists(
		"Item Price", {"item_code": SPARE_ITEM_CODE, "price_list": "Standard Buying"}
	):
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": SPARE_ITEM_CODE,
				"price_list": "Standard Buying",
				"price_list_rate": 1800,
				"buying": 1,
			}
		).insert(ignore_permissions=True)

	# Solution -> spare mapping so the SR spare line passes cascade checks
	if not frappe.db.exists(
		"Solution Spare Mapping",
		{"repair_solution": "Post-Liquid Component Replacement", "spare_item": SPARE_ITEM_CODE},
	):
		frappe.get_doc(
			{
				"doctype": "Solution Spare Mapping",
				"repair_solution": "Post-Liquid Component Replacement",
				"issue_category": "Water Damage",
				"spare_item": SPARE_ITEM_CODE,
				"default_qty": 1,
				"uom": "Nos",
				"is_mandatory": 0,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
	_step("solution-spare mapping", "Post-Liquid Component Replacement -> keyboard")

	# Billing service item used by POS collect_repair_payment ------------
	if not frappe.db.exists("Item", "Repair Service"):
		gc = frappe.db.get_value("Item", "GCOTSR", ["ch_category", "ch_sub_category"], as_dict=True)
		it = frappe.new_doc("Item")
		it.item_code = "Repair Service"
		it.item_name = "Repair Service"
		it.description = "GoFix repair service charge"
		it.item_group = "Services"
		it.stock_uom = "Nos"
		it.is_stock_item = 0
		it.is_sales_item = 1
		it.ch_category = gc.ch_category
		it.ch_sub_category = gc.ch_sub_category
		it.gst_hsn_code = frappe.db.get_value("Item", "GCOTSR", "gst_hsn_code") or "998717"
		it.insert(ignore_permissions=True)
		if it.meta.has_field("ch_lifecycle_status") and it.ch_lifecycle_status != "Active":
			frappe.db.set_value("Item", it.name, "ch_lifecycle_status", "Active", update_modified=False)
	_step("billing item", "Repair Service")

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE0 OK")


# ---------------------------------------------------------------------------
# Phase 1 — custody receipt + POS Service Intake at Ashok Nagar
# ---------------------------------------------------------------------------


def phase1():
	st = _state()

	# Custody Material Receipt: the customer's laptop enters store stock so
	# the physical hub transfer can ride the manifest flow (system models
	# device custody as stock of the device item).
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Receipt"
	se.company = COMPANY
	se.posting_date = nowdate()
	se.remarks = "GoFix custody receipt — customer device (Karthik Raman, water-damaged HP Pavilion 15)"
	se.append(
		"items",
		{
			"item_code": DEVICE_ITEM,
			"qty": 1,
			"t_warehouse": STORE_WH,
			"basic_rate": 35000,
			"use_serial_batch_fields": 1,
			"serial_no": DEVICE_SERIAL,
		},
	)
	se.flags.ignore_procurement_guardrails = True
	se.insert(ignore_permissions=True)
	se.submit()
	st["custody_receipt"] = se.name
	_step("custody receipt", se.name)

	from ch_pos.api.repair import create_service_intake_from_pos

	res = create_service_intake_from_pos(
		{
			"customer": st["customer"],
			"contact_number": "9841027365",
			"device_item": DEVICE_ITEM,
			"serial_no": DEVICE_SERIAL,
			"issue_category": "Water Damage",
			"issue_lines": [
				{"issue_category": "Water Damage", "reported_by": "Customer", "status": "Open"}
			],
			"issue_description": (
				"Rainwater spilled on laptop; not switching on. Keyboard keys sticky, "
				"corrosion suspected. Customer needs data preserved."
			),
			"warranty_status": "Out of Warranty",
			"device_condition": "Water Damaged",
			"accessories_received": "Charger, laptop bag",
			"data_backup_disclaimer": 1,
			"mode_of_service": "Walk-in",
			"company": COMPANY,
			"source_warehouse": STORE_WH,
			"priority": "High",
		}
	)
	st["sr"] = res["name"]
	_step("service request (POS, submitted)", f"{res['name']} status={res['status']}")

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE1 OK")


# ---------------------------------------------------------------------------
# Phase 2 — analysis, solutions, estimate, customer approval -> Service Order
# ---------------------------------------------------------------------------


def phase2():
	st = _state()
	sr_name = st["sr"]

	from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import confirm_analysis

	confirm_analysis(sr_name)
	_step("analysis confirmed", sr_name)

	if frappe.db.has_column("Service Request", "repairability_status"):
		frappe.db.set_value("Service Request", sr_name, "repairability_status", "Repairable", update_modified=False)

	sr = frappe.get_doc("Service Request", sr_name)
	sr.flags.ignore_validate_update_after_submit = True
	sr.flags.ignore_mandatory = True
	for sol in ("Liquid Damage Treatment", "Post-Liquid Component Replacement"):
		row = frappe.db.get_value(
			"Repair Solution", sol, ["issue_category", "solution_code", "estimated_minutes", "requires_spare"], as_dict=True
		)
		sr.append(
			"solution_lines",
			{
				"repair_solution": sol,
				"issue_category": row.issue_category,
				"solution_code": row.solution_code,
				"estimated_minutes": row.estimated_minutes,
				"requires_spare": row.requires_spare,
				"status": "Planned",
			},
		)
	sr.save(ignore_permissions=True)
	_step("solution lines", "Liquid Damage Treatment + Post-Liquid Component Replacement")

	from gofix.gofix_services import orchestration

	est = orchestration.create_estimate_version(sr_name, reason=None, send_to_customer=True)
	_step("estimate v1", est)

	appr = orchestration.customer_approve_estimate(sr_name, remarks="Customer approved on call")
	_step("estimate approved", appr)

	so_name = frappe.db.get_value("Service Request", sr_name, "service_order")
	if not so_name:
		sr = frappe.get_doc("Service Request", sr_name)
		so_name = sr.create_service_order()
	st["so"] = so_name
	_step("service order", so_name)

	# High-Estimate approval rule may have parked the SO pending approval
	if frappe.db.get_value("Sales Order", so_name, "estimate_approval_status") == "Pending":
		from gofix.gofix_services.api import customer_approve_estimate as so_approve

		so_approve(so_name, remarks="Approved with estimate v1")
		_step("SO estimate approval", "Approved")

	# Accept: customer leaves the device with us
	updates = {"decision": "Accepted", "walkin_status": "Accepted", "status": "In Service"}
	if frappe.db.has_column("Service Request", "accepted_by"):
		updates["accepted_by"] = frappe.session.user
	frappe.db.set_value("Service Request", sr_name, updates, update_modified=False)
	_step("SR accepted", "decision=Accepted, status=In Service")

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE2 OK")


# ---------------------------------------------------------------------------
# Phase 3 — assign the repair job to the technician
# ---------------------------------------------------------------------------


def phase3():
	st = _state()
	from gofix.gofix_services.doctype.job_assignment.job_assignment import (
		create_job_sheet_from_service_order,
	)

	ja = create_job_sheet_from_service_order(
		st["so"], service_engineer=st["technician"], job_type="Repair", estimated_hours=6
	)
	ja_name = ja if isinstance(ja, str) else getattr(ja, "name", ja)
	st["ja"] = ja_name
	_step("job assignment", f"{ja_name} (In Progress, est 6h)")

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE3 OK")


# ---------------------------------------------------------------------------
# Phase 4 — device transfer Ashok Nagar -> Vepery Hub with full logistics leg
# ---------------------------------------------------------------------------


def _submit_transfer_se(se_name):
	se_doc = frappe.get_doc("Stock Entry", se_name)
	if se_doc.docstatus == 0:
		se_doc.custom_status = None
		se_doc.flags.ignore_permissions = True
		se_doc.flags.ignore_procurement_guardrails = True
		se_doc.submit()
	return se_doc


def repair_phase7():
	"""Recover from the custom_status ledger no-op + finish the return leg."""
	st = _state()
	from ch_logistics.api import logistics_api, transfer_manifest_api

	# 1) Outbound SE posted no SLEs (custom_status suppressed the ledger).
	#    Cancel it, post a clean transfer, repoint the closed manifest row.
	old_se = "GFNAMT26000007"
	if frappe.db.get_value("Stock Entry", old_se, "docstatus") == 1 and not frappe.db.exists(
		"Stock Ledger Entry", {"voucher_no": old_se, "is_cancelled": 0}
	):
		se_doc = frappe.get_doc("Stock Entry", old_se)
		se_doc.flags.ignore_permissions = True
		se_doc.flags.ignore_links = True
		se_doc.cancel()
		_step("cancelled ledgerless outbound SE", old_se)
		new_se = _make_device_transfer_se(
			STORE_WH, HUB_WH, f"GoFix device transfer {st['sr']}: Ashok Nagar -> Vepery Hub (reposted)"
		)
		_submit_transfer_se(new_se.name)
		row = frappe.get_all(
			"CH Transfer Manifest Item", filters={"parent": st["tm_out"]}, pluck="name", limit=1
		)
		if row:
			frappe.db.set_value("CH Transfer Manifest Item", row[0], "stock_entry", new_se.name, update_modified=False)
		st["se_out"] = new_se.name
		_step("outbound transfer reposted", f"{new_se.name} (manifest {st['tm_out']} repointed)")

	# 2) Continue the return manifest from wherever it stalled.
	tm = st.get("tm_back") or "TM-2026-00007"
	trip = st.get("trip_back") or "TRIP-2026-00007"
	se_back = st.get("se_back") or "GFNAMT26000009"
	status = frappe.db.get_value("CH Transfer Manifest", tm, "status")
	src_geo = _geo(HUB_WH, HUB_GEO)
	dst_geo = _geo(STORE_WH, STORE_GEO)
	qr = frappe.db.get_value("CH Transfer Manifest", tm, "qr_payload") or tm
	if frappe.db.get_value("CH Logistics Trip", trip, "status") == "Assigned":
		logistics_api.trip_start(trip)
	if status == "Assigned":
		transfer_manifest_api.start_pickup(
			tm, pickup_photo="/files/e2e_pickup.jpg", lat=src_geo[0], lng=src_geo[1], scanned_qr=qr
		)
		status = "In Transit"
	if status == "In Transit":
		transfer_manifest_api.mark_reached_destination(tm, lat=dst_geo[0], lng=dst_geo[1])
		otp = frappe.db.get_value("CH Transfer Manifest", tm, "delivery_otp")
		transfer_manifest_api.complete_delivery(
			tm,
			delivery_photo="/files/e2e_delivery.jpg",
			receiver_name="Ashok Nagar Front Desk",
			otp=otp,
			lat=dst_geo[0],
			lng=dst_geo[1],
			scanned_qr=qr,
		)
		status = "Delivered"
	if status == "Delivered":
		_submit_transfer_se(se_back)
		transfer_manifest_api.accept_delivery(tm)
		transfer_manifest_api.close_manifest(tm)
	tstat = frappe.db.get_value("CH Logistics Trip", trip, "status")
	if tstat == "Started":
		logistics_api.trip_complete(trip)
		tstat = frappe.db.get_value("CH Logistics Trip", trip, "status")
	if tstat == "Completed":
		logistics_api.trip_close(trip)
	_step("return logistics", f"{tm} -> {frappe.db.get_value('CH Transfer Manifest', tm, 'status')}, {trip} -> {frappe.db.get_value('CH Logistics Trip', trip, 'status')}")

	from gofix.gofix_services.api import complete_service_transfer_return

	if frappe.db.get_value("Service Request", st["sr"], "transfer_status") == "Return In Transit":
		complete_service_transfer_return(st["sr"])
	_step("SR transfer", frappe.db.get_value("Service Request", st["sr"], "transfer_status"))

	st["tm_back"] = tm
	st["trip_back"] = trip
	st["se_back"] = se_back
	_save(st)
	frappe.db.commit()
	print("[E2E] REPAIR7 OK")


def _geo(warehouse, fallback):
	"""Coordinates exactly as the manifest geofence resolves them."""
	try:
		from ch_logistics.api.optimizer import _warehouse_coords

		coords = _warehouse_coords(warehouse)
		if coords:
			return coords
	except Exception:
		pass
	return fallback


def _make_device_transfer_se(src, dst, remark):
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Transfer"
	se.company = COMPANY
	se.posting_date = nowdate()
	se.remarks = remark
	se.append(
		"items",
		{
			"item_code": DEVICE_ITEM,
			"qty": 1,
			"s_warehouse": src,
			"t_warehouse": dst,
			"use_serial_batch_fields": 1,
			"serial_no": DEVICE_SERIAL,
		},
	)
	se.insert(ignore_permissions=True)  # stays Draft — manifest submits it on delivery acceptance
	return se


def _run_logistics_leg(st, se_name, src, dst, receiver, trip_key, tm_key, direction="Forward"):
	from ch_logistics.api import logistics_api, transfer_manifest_api

	tm_name = transfer_manifest_api.create_manifest(
		[se_name], source_warehouse=src, destination_warehouse=dst
	)
	tm_name = tm_name if isinstance(tm_name, str) else tm_name.get("name")
	if direction != "Forward":
		frappe.db.set_value("CH Transfer Manifest", tm_name, "direction", direction, update_modified=False)
	transfer_manifest_api.pack_box(
		tm_name,
		packed_qty=1,
		weight_kg=2.8,
		dimensions_cm="45x35x10",
		seal_number=f"SEAL-{tm_name[-4:]}",
		packing_photo="/files/e2e_packing.jpg",
	)
	tm = frappe.get_doc("CH Transfer Manifest", tm_name)
	tm.submit()  # -> Packed: appears in logistics control tower unassigned worklist
	_step(f"manifest {tm_key}", f"{tm_name} Packed (in control-tower dispatcher queue)")

	trip = frappe.get_doc(
		{
			"doctype": "CH Logistics Trip",
			"trip_date": today(),
			"company": COMPANY,
			"driver": st["driver"],
			"vehicle": st["vehicle"],
			"planned_start": add_to_date(now_datetime(), minutes=15),
			"planned_end": add_to_date(now_datetime(), hours=3),
			"direction": direction,
			"status": "Assigned",
			"stops": [
				{"sequence": 1, "warehouse": src, "stop_type": "Pickup"},
				{"sequence": 2, "warehouse": dst, "stop_type": "Drop"},
			],
		}
	)
	trip.insert(ignore_permissions=True)
	_step(f"trip {trip_key}", trip.name)

	logistics_api.attach_manifests(trip.name, [tm_name])
	plate = frappe.db.get_value("Vehicle", st["vehicle"], "license_plate")
	# Attaching to a driver-assigned trip can fan the driver onto the manifest
	# automatically — only assign explicitly if it is still Packed.
	if frappe.db.get_value("CH Transfer Manifest", tm_name, "status") == "Packed":
		transfer_manifest_api.assign_driver(
			tm_name, driver=st["driver"], vehicle_number=plate, vehicle=st["vehicle"]
		)
	if not frappe.db.get_value("CH Transfer Manifest", tm_name, "vehicle_number"):
		frappe.db.set_value("CH Transfer Manifest", tm_name, "vehicle_number", plate, update_modified=False)
	if frappe.db.get_value("CH Logistics Trip", trip.name, "status") == "Draft":
		logistics_api.trip_assign_driver(trip.name, st["driver"], vehicle=st["vehicle"])
	if frappe.db.get_value("CH Logistics Trip", trip.name, "status") == "Assigned":
		logistics_api.trip_start(trip.name)

	src_geo = _geo(src, STORE_GEO if src == STORE_WH else HUB_GEO)
	dst_geo = _geo(dst, STORE_GEO if dst == STORE_WH else HUB_GEO)
	qr = frappe.db.get_value("CH Transfer Manifest", tm_name, "qr_payload") or tm_name
	transfer_manifest_api.start_pickup(
		tm_name,
		pickup_photo="/files/e2e_pickup.jpg",
		lat=src_geo[0],
		lng=src_geo[1],
		notes="Device boxed and sealed",
		scanned_qr=qr,
	)
	transfer_manifest_api.mark_reached_destination(tm_name, lat=dst_geo[0], lng=dst_geo[1])
	otp = frappe.db.get_value("CH Transfer Manifest", tm_name, "delivery_otp")
	transfer_manifest_api.complete_delivery(
		tm_name,
		delivery_photo="/files/e2e_delivery.jpg",
		receiver_name=receiver,
		otp=otp,
		lat=dst_geo[0],
		lng=dst_geo[1],
		scanned_qr=qr,
	)
	# Submit the transfer SE ourselves (no custom_status — the transit
	# workflow's manual SLE path stays out of the way, so ERPNext posts the
	# stock ledger normally). accept_delivery then just verifies it.
	_submit_transfer_se(se_name)
	transfer_manifest_api.accept_delivery(tm_name)
	transfer_manifest_api.close_manifest(tm_name)
	# close_manifest may auto-complete/close the parent trip — finish only
	# whatever is left.
	tstat = frappe.db.get_value("CH Logistics Trip", trip.name, "status")
	if tstat == "Started":
		logistics_api.trip_complete(trip.name)
		tstat = frappe.db.get_value("CH Logistics Trip", trip.name, "status")
	if tstat == "Completed":
		logistics_api.trip_close(trip.name)
	_step(f"logistics {tm_key}", f"{tm_name} Closed, {trip.name} Closed")

	st[tm_key] = tm_name
	st[trip_key] = trip.name
	return tm_name, trip.name


def phase4():
	st = _state()
	sr_name = st["sr"]

	from gofix.gofix_services.api import create_service_transfer, receive_service_transfer

	if frappe.db.get_value("Service Request", sr_name, "transfer_status") != "In Transit":
		create_service_transfer(
			sr_name, HUB_WH, reason="Water damage — board-level treatment needs Vepery hub lab"
		)
	_step("SR transfer", "In Transit -> Vepery Hub")

	se = _make_device_transfer_se(
		STORE_WH, HUB_WH, f"GoFix device transfer {sr_name}: Ashok Nagar -> Vepery Hub (water damage lab)"
	)
	st["se_out"] = se.name
	_step("device transfer SE (draft)", se.name)

	_run_logistics_leg(
		st, se.name, STORE_WH, HUB_WH, receiver="Hub Inward Desk — Vepery", trip_key="trip_out", tm_key="tm_out"
	)

	receive_service_transfer(sr_name)
	_step("SR transfer", "Received at Service Center (Vepery Hub)")

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE4 OK")


# ---------------------------------------------------------------------------
# Phase 5 — spare request -> Material Request -> PO -> Purchase Receipt -> consume
# ---------------------------------------------------------------------------


def phase5():
	st = _state()
	sr_name = st["sr"]

	from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import (
		add_spare_to_ticket,
		raise_material_request,
		release_spare_reservation,
	)

	sr0 = frappe.get_doc("Service Request", sr_name)
	if not [r for r in sr0.spare_lines if r.spare_item == SPARE_ITEM_CODE]:
		r1 = add_spare_to_ticket(
			sr_name, SPARE_ITEM_CODE, qty=1, rate=2500, repair_solution="Post-Liquid Component Replacement"
		)
		_step("spare request", r1)

	mr_name = frappe.db.get_value(
		"Material Request", {"service_request": sr_name, "docstatus": 1}, "name"
	)
	if not mr_name:
		mr = raise_material_request(sr_name)
		mr_name = mr.get("material_request") if isinstance(mr, dict) else mr
	st["mr"] = mr_name
	_step("material request", mr_name)

	from gofix.purchase_api import create_pos_from_material_request

	po_res = create_pos_from_material_request(mr_name)
	_step("po result", po_res)
	po_names = po_res.get("purchase_orders") or po_res.get("pos") or po_res.get("created") or []
	if isinstance(po_names, str):
		po_names = [po_names]
	if not po_names:
		po_names = frappe.get_all(
			"Purchase Order",
			filters={"company": COMPANY, "docstatus": ["<", 2]},
			or_filters=[["Purchase Order Item", "material_request", "=", mr_name]],
			pluck="name",
			limit=1,
		)
	po_name = po_names[0]
	po = frappe.get_doc("Purchase Order", po_name)
	if po.docstatus == 0:
		po.flags.ignore_procurement_guardrails = True
		po.submit()
	st["po"] = po_name
	_step("purchase order", f"{po_name} (supplier {po.supplier}, {po.grand_total})")

	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	pr = make_purchase_receipt(po_name)
	pr.flags.ignore_procurement_guardrails = True
	pr.insert(ignore_permissions=True)
	pr.submit()
	st["pr"] = pr.name
	_step("purchase receipt", f"{pr.name} at {pr.items[0].warehouse}")

	# Stock has landed — release the awaiting line and consume the spare.
	sr = frappe.get_doc("Service Request", sr_name)
	awaiting = [r for r in sr.spare_lines if r.status == "Awaiting Procurement"]
	for row in awaiting:
		release_spare_reservation(sr_name, row.name)
	r2 = add_spare_to_ticket(
		sr_name, SPARE_ITEM_CODE, qty=1, rate=2500, repair_solution="Post-Liquid Component Replacement"
	)
	_step("spare consumed", r2)
	sr = frappe.get_doc("Service Request", sr_name)
	consumed = [r for r in sr.spare_lines if r.status == "Consumed"]
	if consumed:
		st["spare_se"] = consumed[0].get("custom_stock_entry")
		_step("spare issue SE", st["spare_se"])

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE5 OK")


# ---------------------------------------------------------------------------
# Phase 6 — technician repair time (5h) and job completion
# ---------------------------------------------------------------------------


def phase6():
	st = _state()

	ja = frappe.get_doc("Job Assignment", st["ja"])
	ja.start_datetime = add_to_date(now_datetime(), hours=-5)
	ja.end_datetime = now_datetime()
	ja.assignment_status = "Completed"
	ja.repair_outcome = "Repaired"
	ja.technician_remarks = (
		"Ultrasonic board clean + corrosion treatment done at hub lab. "
		"Keyboard assembly replaced. 24h soak test passed. Data intact."
	)
	ja.save(ignore_permissions=True)
	if ja.docstatus == 0:
		ja.submit()
	_step("job completed", f"{ja.name} actual_hours={ja.actual_hours}")

	from gofix.gofix_services.api import calculate_suggested_price

	price = calculate_suggested_price(st["so"])
	_step("suggested pricing", price)

	so_state = frappe.db.get_value("Sales Order", st["so"], ["qc_status", "workflow_state"])
	_step("service order state", so_state)

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE6 OK")


# ---------------------------------------------------------------------------
# Phase 7 — QC pass at hub, return logistics leg, device back at Ashok Nagar
# ---------------------------------------------------------------------------


def phase7():
	st = _state()
	sr_name = st["sr"]

	from gofix.gofix_services.page.gofix_ops_hub.gofix_ops_hub import complete_qc

	qc = complete_qc(sr_name, "Pass")
	_step("QC", qc)

	from gofix.gofix_services.api import complete_service_transfer_return, return_service_transfer

	return_service_transfer(sr_name)
	se = _make_device_transfer_se(
		HUB_WH, STORE_WH, f"GoFix device return {sr_name}: Vepery Hub -> Ashok Nagar (repair complete)"
	)
	st["se_back"] = se.name
	_step("return transfer SE (draft)", se.name)

	_run_logistics_leg(
		st,
		se.name,
		HUB_WH,
		STORE_WH,
		receiver="Ashok Nagar Front Desk",
		trip_key="trip_back",
		tm_key="tm_back",
		direction="Reverse",
	)

	complete_service_transfer_return(sr_name)
	_step("SR transfer", "Returned to Store (Ashok Nagar)")

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE7 OK")


# ---------------------------------------------------------------------------
# Phase 8 — billing & payment at the Ashok Nagar POS
# ---------------------------------------------------------------------------


def phase8():
	st = _state()
	sr_name = st["sr"]

	from ch_pos.api.pos_api import collect_repair_payment

	# Store opens the day's POS session (required by the Maker-Checker
	# "POS Direct Submit" workflow edge).
	from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session

	if not (get_active_session(POS_PROFILE) or {}).get("name"):
		from ch_pos.api.session_api import open_session

		sess = open_session(POS_PROFILE, opening_cash=5000)
		_step("POS session opened", sess)

	# 5h labour @ 400 = 2000 + keyboard spare 2500 = 4500
	res = collect_repair_payment(
		service_request=sr_name,
		amount=4500,
		mode_of_payment="Cash",
		pos_profile=POS_PROFILE,
		customer=st["customer"],
		service_order=st["so"],
	)
	st["invoice"] = res.get("invoice")
	_step("POS invoice", res)

	sr = frappe.db.get_value(
		"Service Request",
		sr_name,
		["status", "decision", "service_invoice", "transfer_status", "current_location"],
		as_dict=True,
	)
	_step("final SR state", sr)

	_save(st)
	frappe.db.commit()
	print("[E2E] PHASE8 OK — full trail:")
	for k, v in sorted(_state().items()):
		print(f"    {k}: {v}")


def run_phase(phase):
	fn = globals()[f"phase{phase}"]
	try:
		fn()
	except Exception:
		traceback.print_exc()
		raise
