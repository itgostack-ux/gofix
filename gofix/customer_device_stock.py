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


def is_customer_device_bin(warehouse: str | None) -> bool:
	"""True if `warehouse` is a store's Customer Device custody bin."""
	if not warehouse:
		return False
	return f"-{BIN_SUFFIX}" in warehouse or warehouse.endswith(BIN_SUFFIX)


def sellable_bin_for(warehouse: str | None) -> str | None:
	"""The Sellable bin of the store that owns `warehouse` — what a purchase
	destination should almost always be."""
	if not warehouse:
		return None
	store = frappe.db.get_value("CH Store", {"warehouse": warehouse}, "warehouse")
	if store:
		return store
	# Given a custody/quarantine bin, climb to the store and take its Sellable one.
	group = frappe.db.get_value("Warehouse", warehouse, "parent_warehouse")
	if group:
		return frappe.db.get_value("CH Store", {"warehouse_group": group}, "warehouse")
	return None


def block_customer_device_as_destination(doc, method=None):
	"""A purchase must never land in a Customer Device bin.

	That bin is customer special stock: quantity tracked, **value nil**, and the
	goods in it belong to the customer who handed them in. Receiving supplier
	inventory there mixes owned, valued stock into a bin whose whole premise is
	that nothing in it is ours — the device custody trail and the stock
	valuation both stop meaning anything.

	Checked on the header warehouse and on every line, because either can carry
	its own.
	"""
	offending = set()
	for field in ("set_warehouse", "warehouse", "to_warehouse"):
		value = doc.get(field)
		if is_customer_device_bin(value):
			offending.add(value)
	for row in doc.get("items") or []:
		for field in ("warehouse", "t_warehouse", "receiving_warehouse"):
			value = row.get(field)
			if is_customer_device_bin(value):
				offending.add(value)

	if not offending:
		return

	wrong = sorted(offending)[0]
	suggestion = sellable_bin_for(wrong)
	hint = (
		_(" Use {0} instead.").format(frappe.bold(suggestion)) if suggestion else ""
	)
	frappe.throw(
		_("{0} is a Customer Device custody bin — it holds handsets belonging to "
		  "customers, at nil value, and cannot receive purchased stock.{1}")
		.format(frappe.bold(wrong), hint),
		title=_("Invalid Destination Warehouse"),
	)


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


# Tickets whose device is still in the building. A delivered or abandoned repair
# has nothing left to take custody of.
OPEN_DECISIONS = ("Draft", "Accepted", "In Service", "In Progress", "On Hold", "Completed")


@frappe.whitelist(methods=["POST"])
def backfill_customer_device_custody(company=None, limit: int = 500, dry_run: int = 1) -> dict:
    """Take custody of devices on tickets that predate the setting.

    Custody is posted at intake, so every ticket opened before tracking was
    switched on is holding a device the stock system knows nothing about — it
    cannot be moved, cannot go on a manifest, and cannot be dispatched. Those
    tickets do not heal themselves; somebody has to receive what is already on
    the shelf.

    Defaults to a DRY RUN, because this posts real stock entries against live
    tickets and the count should be looked at before it happens.

    Devices are received where the ticket says they are, which for an open
    repair is its current location or the store that raised it.
    """
    frappe.has_permission("Service Request", "write", throw=True)
    frappe.has_permission("Stock Entry", "create", throw=True)

    if not cint(frappe.db.get_single_value("GoFix Settings", "track_customer_devices")):
        frappe.throw(
            _("Customer device tracking is switched off, so there is nothing to back-fill. "
              "Turn it on in GoFix Settings first."),
            title=_("Tracking Disabled"),
        )

    filters = {
        "docstatus": 1,
        "decision": ("in", list(OPEN_DECISIONS)),
        "customer_device_entry": ("is", "not set"),
    }
    if company:
        filters["company"] = company

    rows = frappe.get_all(
        "Service Request",
        filters=filters,
        fields=["name", "device_item", "device_item_name", "serial_no", "company",
                "source_warehouse", "current_location", "decision"],
        limit_page_length=cint(limit) or 500,
        order_by="creation asc",
    )

    done, skipped = [], []
    for row in rows:
        if not row.device_item:
            skipped.append((row.name, _("no device on the ticket")))
            continue
        if not cint(frappe.db.get_value("Item", row.device_item, "is_stock_item")):
            skipped.append((row.name, _("device is not a stock item")))
            continue

        where = row.current_location or row.source_warehouse
        target = customer_device_bin(where, row.company)
        if not target:
            skipped.append((row.name, _("no custody bin for {0}").format(where)))
            continue

        if cint(dry_run):
            done.append((row.name, target))
            continue

        sr = frappe.get_doc("Service Request", row.name)
        entry = receive_customer_device(sr, warehouse=where)
        (done if entry else skipped).append(
            (row.name, target if entry else _("custody posting failed — see the error log"))
        )

    return {
        "dry_run": bool(cint(dry_run)),
        "considered": len(rows),
        "taken_into_custody": len(done),
        "skipped": len(skipped),
        "detail": [{"service_request": n, "note": str(w)} for n, w in done[:50]],
        "skipped_detail": [{"service_request": n, "reason": str(w)} for n, w in skipped[:50]],
    }

