# Copyright (c) 2026, GoFix and contributors

"""Holding a customer's device without pretending to own it.

A handset booked in for repair is not ours. We did not buy it, it has no
valuation, and it must never touch the balance sheet. But it IS a physical
object in our custody that has to be found, moved between sites, handed to a
driver, scanned at a door and given back — and a thing the stock system knows
nothing about can do none of that.

Every serious ERP resolves this the same way: **customer special stock**. SAP
posts the device to a customer-owned special stock segment; the material is
tracked, batched, moved and counted like anything else, and its value is nil so
no financial statement moves. This is the same idea in ERPNext's vocabulary:

  * a ``Customer Device`` bin per store, alongside Sellable / Damaged / Demo
  * the device received into it at intake, quantity 1, **rate 0**
  * transfers between sites moving Customer Device bin -> Customer Device bin,
    which is what puts a repair on a manifest with a driver
  * the device issued back out when it goes home to its owner

The alternative — what this replaces — was posting nothing at all, which kept
the ledger honest and left the device invisible: no manifest, no trip, no driver
app, no custody scan, and a serial sitting in warehouse ``None`` that broke
every movement that touched it.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

BIN_TYPE = "Customer Device"
BIN_SUFFIX = "CustomerDevice"


def customer_device_bin(store_warehouse: str, company: str | None = None) -> str | None:
	"""The Customer Device bin belonging to the same store as `store_warehouse`.

	Accepts any of the store's warehouses — Sellable, Damaged, the store group —
	because callers hold whichever one the ticket happens to carry.
	"""
	if not store_warehouse:
		return None

	store = frappe.db.get_value(
		"CH Store",
		{"warehouse": store_warehouse},
		["name", "warehouse_group", "company"],
		as_dict=True,
	)
	if not store:
		# Given a bin or the group, climb to the store that owns it.
		node, seen = store_warehouse, set()
		while node and node not in seen:
			seen.add(node)
			store = frappe.db.get_value(
				"CH Store", {"warehouse_group": node},
				["name", "warehouse_group", "company"], as_dict=True,
			)
			if store:
				break
			node = frappe.db.get_value("Warehouse", node, "parent_warehouse")
	if not store:
		return None

	bin_name = frappe.db.get_value(
		"Warehouse",
		{
			"company": store.company or company,
			"ch_bin_type": BIN_TYPE,
			"parent_warehouse": store.warehouse_group,
			"disabled": 0,
		},
		"name",
	)
	if bin_name:
		return bin_name

	# ensure_store_bins deliberately skips hubs — Damaged / Demo / Buyback are
	# retail stock states and a distribution centre has no business with them.
	# Custody is different: a repair hub is exactly where customer devices end
	# up. Rather than bend that rule in another app, the bin is created here, on
	# demand, by the module that needs it.
	return _create_customer_device_bin(store, company)


def _create_customer_device_bin(store, company: str | None = None) -> str | None:
	"""Create the store's Customer Device bin. Idempotent by name."""
	parent = store.get("warehouse_group")
	owner_company = store.get("company") or company
	if not (parent and owner_company):
		return None

	prefix = store.name
	warehouse_name = f"{prefix}-{BIN_SUFFIX}"
	abbr = frappe.db.get_value("Company", owner_company, "abbr")
	full = f"{warehouse_name} - {abbr}" if abbr else warehouse_name
	if frappe.db.exists("Warehouse", full):
		return full

	try:
		wh = frappe.new_doc("Warehouse")
		wh.warehouse_name = warehouse_name
		wh.company = owner_company
		wh.parent_warehouse = parent
		wh.is_group = 0
		if wh.meta.get_field("ch_location_type"):
			wh.ch_location_type = "Store Bin"
		if wh.meta.get_field("ch_bin_type"):
			wh.ch_bin_type = BIN_TYPE
		if wh.meta.get_field("ch_store"):
			wh.ch_store = store.name
		wh.flags.ignore_permissions = True
		wh.insert(ignore_permissions=True)
		frappe.logger("gofix").info(f"GoFix: created custody bin {wh.name}")
		return wh.name
	except Exception:
		frappe.log_error(
			frappe.get_traceback(), f"GoFix: could not create a custody bin for {store.name}"
		)
		return None


def is_customer_device(sr) -> bool:
	"""True when the ticket's device belongs to the customer, not to us.

	A walk-in repair is the customer's; a demo unit or a buyback handset the
	company is repairing for itself is not, and must not be diverted into
	customer special stock.
	"""
	if not sr.get("device_item"):
		return False
	# A device we own is already standing in one of our own bins.
	serial = (sr.get("serial_no") or "").strip()
	if serial and frappe.db.exists("Serial No", serial):
		warehouse = frappe.db.get_value("Serial No", serial, "warehouse")
		if warehouse and frappe.db.get_value("Warehouse", warehouse, "ch_bin_type") != BIN_TYPE:
			return False
	return True


def receive_customer_device(sr, warehouse: str | None = None) -> str | None:
	"""Take the device into custody as customer special stock.

	Quantity 1 at rate 0: the ledger records that we are holding it, and the
	valuation stays nil because we do not own it. Never raises — a repair must
	not be refused because the custody posting failed, and the ticket remains
	the primary record either way.
	"""
	if not cint(frappe.db.get_single_value("GoFix Settings", "track_customer_devices") or 0):
		return None
	if sr.get("customer_device_entry"):
		return sr.get("customer_device_entry")
	if not is_customer_device(sr):
		return None

	target = customer_device_bin(warehouse or sr.get("source_warehouse"), sr.get("company"))
	if not target:
		return None

	try:
		item = sr.device_item
		if not cint(frappe.db.get_value("Item", item, "is_stock_item")):
			return None

		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.company = sr.company
		se.remarks = _("Customer device taken into custody for repair {0}").format(sr.name)
		row = {
			"item_code": item,
			"qty": 1,
			"t_warehouse": target,
			"basic_rate": 0,
			# The device has no cost to us; without this ERPNext refuses a
			# zero-rate receipt for a valuated item.
			"allow_zero_valuation_rate": 1,
		}
		if (sr.get("serial_no") or "").strip():
			row["serial_no"] = sr.serial_no.strip()
		se.append("items", row)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()

		if sr.meta.get_field("customer_device_entry"):
			sr.db_set("customer_device_entry", se.name, update_modified=False)
		if sr.meta.get_field("customer_device_warehouse"):
			sr.db_set("customer_device_warehouse", target, update_modified=False)
		return se.name
	except Exception:
		frappe.log_error(
			frappe.get_traceback(), f"GoFix: could not take custody of the device on {sr.name}"
		)
		return None


def release_customer_device(sr, reason: str | None = None) -> str | None:
	"""Give the device back — issue it out of custody.

	Called when the handset goes home to its owner. Without this the Customer
	Device bin only ever grows, and a count of what we are holding becomes
	meaningless.
	"""
	entry = sr.get("customer_device_entry")
	if not entry:
		return None

	held_at = sr.get("customer_device_warehouse") or customer_device_bin(
		sr.get("current_location") or sr.get("source_warehouse"), sr.get("company")
	)
	if not held_at:
		return None

	try:
		if flt(frappe.db.get_value(
			"Bin", {"item_code": sr.device_item, "warehouse": held_at}, "actual_qty"
		)) <= 0:
			return None

		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Issue"
		se.company = sr.company
		se.remarks = reason or _("Customer device returned to its owner — repair {0}").format(sr.name)
		row = {
			"item_code": sr.device_item,
			"qty": 1,
			"s_warehouse": held_at,
			"allow_zero_valuation_rate": 1,
		}
		if (sr.get("serial_no") or "").strip():
			row["serial_no"] = sr.serial_no.strip()
		se.append("items", row)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()

		if sr.meta.get_field("customer_device_released_entry"):
			sr.db_set("customer_device_released_entry", se.name, update_modified=False)
		return se.name
	except Exception:
		frappe.log_error(
			frappe.get_traceback(), f"GoFix: could not release the device on {sr.name}"
		)
		return None
