"""GoFix purchase flow helpers — Gap 14: Auto-convert spare parts MR to POs."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate

from gofix.config import get_int_setting
from gofix.scope_guard import assert_warehouse


def _store_for_warehouse(warehouse):
	"""CH Store owning a warehouse (None for hub bins without a store row)."""
	if not warehouse or not frappe.db.table_exists("CH Store"):
		return None
	return frappe.db.get_value("CH Store", {"warehouse": warehouse}) or frappe.db.get_value(
		"CH Store", {"warehouse": warehouse, "disabled": 0}
	)


def reroute_open_spare_procurement(sr, new_warehouse) -> dict:
	"""Auto-change the delivery address of a ticket's open spare procurement
	when the device moves (SAP parts-routing pattern).

	  * Material Requests (un-cancelled) → item warehouses + set_warehouse
	    repointed to the device's new repair location.
	  * Open Purchase Orders traced from those MRs (not fully received) →
	    item warehouses + set_warehouse repointed, so the eventual Purchase
	    Receipt lands where the repair now happens.
	  * Spares ALREADY received at the old location → a draft Material
	    Transfer Stock Entry (old → new) is created for logistics to pick up
	    on the next manifest.

	Every touched document and the SR timeline get a comment. Never raises —
	a rerouting hiccup must not block the device transfer itself.
	"""
	result = {"mrs": [], "pos": [], "transfer_se": None}
	try:
		if isinstance(sr, str):
			sr = frappe.get_doc("Service Request", sr)
		if not new_warehouse:
			return result

		mrs = frappe.get_all(
			"Material Request",
			filters={"service_request": sr.name, "docstatus": ("<", 2), "status": ("not in", ["Stopped", "Cancelled"])},
			pluck="name",
		)
		note = _("Spare delivery redirected to {0} — device moved (Service Request {1}).")

		for mr_name in mrs:
			mr = frappe.get_doc("Material Request", mr_name)
			changed = False
			for row in mr.items:
				if row.warehouse != new_warehouse and flt(row.received_qty) < flt(row.qty):
					frappe.db.set_value("Material Request Item", row.name, "warehouse", new_warehouse, update_modified=False)
					changed = True
			if changed:
				frappe.db.set_value("Material Request", mr_name, "set_warehouse", new_warehouse, update_modified=False)
				mr.add_comment("Comment", note.format(new_warehouse, sr.name))
				result["mrs"].append(mr_name)

			pos = frappe.get_all(
				"Purchase Order Item",
				filters={"material_request": mr_name, "docstatus": 1},
				pluck="parent",
				distinct=True,
			)
			for po_name in set(pos):
				po = frappe.get_doc("Purchase Order", po_name)
				if po.status in ("Closed", "Cancelled") or flt(po.per_received) >= 100:
					continue
				po_changed = False
				for row in po.items:
					if row.material_request == mr_name and row.warehouse != new_warehouse and flt(row.received_qty) < flt(row.qty):
						frappe.db.set_value("Purchase Order Item", row.name, "warehouse", new_warehouse, update_modified=False)
						po_changed = True
				if po_changed:
					po_updates = {"set_warehouse": new_warehouse}
					new_store = _store_for_warehouse(new_warehouse)
					if new_store:
						po_updates["custom_target_store"] = new_store
					frappe.db.set_value("Purchase Order", po_name, po_updates, update_modified=False)
					po.add_comment("Comment", note.format(new_warehouse, sr.name))
					result["pos"].append(po_name)

		# Spares already received at the OLD location and still uncommitted →
		# draft internal transfer for the logistics flow.
		pending_items = {}
		for line in sr.get("spare_lines", []):
			if line.status in ("Awaiting Procurement", "Reserved", "Pending"):
				pending_items[line.spare_item] = pending_items.get(line.spare_item, 0) + flt(line.qty)
		se_rows = []
		for item_code, qty in pending_items.items():
			for bin_row in frappe.get_all(
				"Bin",
				filters={"item_code": item_code, "warehouse": ("!=", new_warehouse), "actual_qty": (">", 0)},
				fields=["warehouse", "actual_qty"],
			):
				move_qty = min(qty, flt(bin_row.actual_qty))
				if move_qty > 0 and frappe.db.get_value("Warehouse", bin_row.warehouse, "company") == sr.company:
					se_rows.append({"item_code": item_code, "qty": move_qty,
						"s_warehouse": bin_row.warehouse, "t_warehouse": new_warehouse})
					break
		if se_rows:
			se = frappe.new_doc("Stock Entry")
			se.stock_entry_type = "Material Transfer"
			se.company = sr.company
			se.remarks = f"Spare re-route for {sr.name}: follow device to {new_warehouse}"
			for r in se_rows:
				se.append("items", r)
			frappe.has_permission("Stock Entry", "create", throw=True)
			se.insert()
			result["transfer_se"] = se.name

		if result["mrs"] or result["pos"] or result["transfer_se"]:
			bits = []
			if result["mrs"]:
				bits.append(_("MR: {0}").format(", ".join(result["mrs"])))
			if result["pos"]:
				bits.append(_("PO: {0}").format(", ".join(result["pos"])))
			if result["transfer_se"]:
				bits.append(_("internal transfer drafted: {0}").format(result["transfer_se"]))
			sr.add_comment(
				"Comment",
				_("Spare delivery address auto-changed to {0} ({1}).").format(new_warehouse, "; ".join(bits)),
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Spare procurement reroute failed for {getattr(sr, 'name', sr)}")
	return result


def mark_spares_in_transit(doc, method=None):
	"""Purchase Order on_submit — the part is ordered, so tell the ticket when.

	Between raising a requisition and the goods landing, a spare line sat on
	"Awaiting Procurement" with no indication that it had even been ordered,
	let alone when it would arrive. The technician could not answer the one
	question the customer asks, and the SLA clock was running against a date
	nobody could see.

	Moves every waiting line on this order to In Transit and stamps the
	promised date from the PO schedule, which is the supplier's commitment
	rather than the lead-time guess the requisition was raised with.
	"""
	try:
		eta_by_item = {}
		mr_names = set()
		for row in doc.get("items") or []:
			if not row.get("material_request"):
				continue
			mr_names.add(row.material_request)
			eta = row.get("schedule_date") or doc.get("schedule_date")
			# a part needed on several rows lands when the LAST of them does
			existing = eta_by_item.get(row.item_code)
			if eta and (not existing or str(eta) > str(existing)):
				eta_by_item[row.item_code] = eta
		if not mr_names:
			return

		for mr_name in mr_names:
			sr_name = frappe.db.get_value("Material Request", mr_name, "service_request")
			if not sr_name:
				continue
			lines = frappe.get_all(
				"SR Spare Line",
				filters={
					"parent": sr_name,
					"parenttype": "Service Request",
					"status": "Awaiting Procurement",
					"material_request": mr_name,
				},
				fields=["name", "spare_item", "item_name", "qty"],
			)
			if not lines:
				continue
			for line in lines:
				frappe.db.set_value(
					"SR Spare Line", line.name,
					{
						"status": "In Transit",
						"purchase_order": doc.name,
						"expected_date": eta_by_item.get(line.spare_item),
					},
					update_modified=False,
				)
			eta_note = eta_by_item.get(lines[0].spare_item)
			frappe.get_doc("Service Request", sr_name).add_comment(
				"Comment",
				_("Spares ordered on {0}: {1}{2}").format(
					doc.name,
					", ".join(f"{l.item_name or l.spare_item} × {l.qty:g}" for l in lines),
					_(" — expected by {0}").format(frappe.utils.formatdate(eta_note)) if eta_note else "",
				),
			)
	except Exception:
		# Never block a purchase order because a ticket could not be annotated.
		frappe.log_error(frappe.get_traceback(), f"GoFix: in-transit update failed for {doc.name}")


def allocate_received_spares_to_tickets(doc, method=None):
	"""Purchase Receipt on_submit hook — close the GRN → ticket loop.

	When a receipt lands for a spare that a Service Request is waiting on
	(spare line "Awaiting Procurement" whose Material Request traces to this
	receipt), flip the line to Reserved, log it on the ticket timeline, and
	ping the assigned technician. Without this, arrived spares sat unnoticed
	until someone manually re-checked the ticket.
	"""
	try:
		item_rows = doc.get("items") or []
		mr_names = {row.get("material_request") for row in item_rows if row.get("material_request")}
		if not mr_names:
			return
		for mr_name in mr_names:
			sr_name = frappe.db.get_value("Material Request", mr_name, "service_request")
			if not sr_name:
				continue
			received_items = {
				row.get("item_code") for row in item_rows if row.get("material_request") == mr_name
			}
			lines = frappe.get_all(
				"SR Spare Line",
				filters={
					"parent": sr_name,
					"parenttype": "Service Request",
					# In Transit as well as Awaiting Procurement: once a purchase
					# order exists the line has already moved on, and matching only
					# the older status would leave it stuck showing a stale ETA.
					"status": ("in", ("Awaiting Procurement", "In Transit")),
					"spare_item": ("in", list(received_items)),
				},
				fields=["name", "spare_item", "item_name", "qty"],
			)
			if not lines:
				continue
			for line in lines:
				# Arrived: it is no longer in transit, so the promised date stops
				# being a forecast and would only mislead if left on screen.
				frappe.db.set_value(
					"SR Spare Line", line.name,
					{"status": "Reserved", "expected_date": None},
					update_modified=False,
				)
			sr = frappe.get_doc("Service Request", sr_name)
			sr.add_comment(
				"Comment",
				_("Spares received via {0}: {1} — reserved for this ticket.").format(
					doc.name, ", ".join(f"{l.item_name or l.spare_item} × {l.qty:g}" for l in lines)
				),
			)
			# Ping the assigned technician (best effort).
			engineer = frappe.db.get_value(
				"Job Assignment",
				{"service_request": sr_name, "docstatus": ("<", 2)},
				"service_engineer",
				order_by="creation desc",
			)
			user = frappe.db.get_value("Employee", engineer, "user_id") if engineer else None
			if user:
				frappe.publish_realtime(
					"msgprint",
					{
						"message": _("Spares for {0} have arrived ({1}).").format(sr_name, doc.name),
						"alert": True,
					},
					user=user,
				)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Spare allocation on {doc.name} failed")


@frappe.whitelist(methods=["POST"])
def create_pos_from_material_request(material_request: str) -> dict:
    """Gap 14: Convert a spare-parts Material Request into POs grouped by default supplier.

    For each item in the MR, looks up the Item Supplier table for the default supplier.
    Creates one PO per supplier, adds all their items, and returns the list of PO names.
    Returns an error message for items with no supplier configured.
    """
    mr = frappe.get_doc("Material Request", material_request)
    mr.check_permission("read")
    frappe.has_permission("Purchase Order", "create", throw=True)
    warehouses = {mr.set_warehouse, *(row.warehouse for row in mr.items)} - {None, ""}
    if not warehouses:
        assert_warehouse(company=mr.company)
    for warehouse in warehouses:
        assert_warehouse(warehouse=warehouse, company=mr.company)
    if mr.docstatus != 1:
        frappe.throw(_("Material Request must be submitted before converting to POs."))
    if mr.material_request_type != "Purchase":
        frappe.throw(_("Only Purchase type Material Requests can be converted to POs."))

    supplier_items: dict[str, list] = {}
    no_supplier = []
    pending_items = [
        item for item in mr.items if (flt(item.qty) - flt(item.ordered_qty)) > 0 and item.item_code
    ]
    item_codes = list(dict.fromkeys(item.item_code for item in pending_items))
    suppliers = {}
    if item_codes:
        for row in frappe.get_all(
            "Item Supplier",
            filters={"parent": ["in", item_codes], "parenttype": "Item"},
            fields=["parent", "supplier"],
            order_by="parent asc, idx asc",
        ):
            suppliers.setdefault(row.parent, row.supplier)

    rates = {}
    if item_codes:
        for row in frappe.get_all(
            "Item Price",
            filters={
                "item_code": ["in", item_codes],
                "price_list": "Standard Buying",
                "selling": 0,
            },
            fields=["item_code", "price_list_rate"],
            order_by="item_code asc, valid_from desc, modified desc",
        ):
            rates.setdefault(row.item_code, flt(row.price_list_rate))

    lead_days = get_int_setting("spare_procurement_lead_days", 3)

    for item in pending_items:
        pending_qty = flt(item.qty) - flt(item.ordered_qty)
        supplier = suppliers.get(item.item_code)
        if not supplier:
            no_supplier.append(item.item_code)
            continue

        supplier_items.setdefault(supplier, []).append({
            "item_code": item.item_code,
            "item_name": item.item_name,
            "description": item.description or item.item_name,
            "qty": pending_qty,
            "uom": item.uom or "Nos",
            "stock_uom": item.stock_uom or "Nos",
            "rate": rates.get(item.item_code, 0),
            "warehouse": item.warehouse or mr.set_warehouse,
            "schedule_date": add_days(nowdate(), lead_days),
            "material_request": material_request,
            "material_request_item": item.name,
        })

    created_pos = []
    for supplier, items in supplier_items.items():
        target_wh = mr.set_warehouse or (items[0].get("warehouse") if items else None)
        po = frappe.get_doc({
            "doctype": "Purchase Order",
            "company": mr.company,
            "supplier": supplier,
            "transaction_date": nowdate(),
            "schedule_date": add_days(nowdate(), lead_days),
            "set_warehouse": target_wh,
            "custom_target_store": _store_for_warehouse(target_wh),
            "custom_purchase_type": "Taxable",  # standard GST purchase of repair spares
            "items": items,
        })
        po.insert()
        created_pos.append(po.name)

    result = {"created": created_pos}
    if no_supplier:
        result["warning"] = _("No default supplier configured for: {0}. "
                              "Set a supplier in the Item Supplier table for these items.").format(
            ", ".join(no_supplier)
        )
    return result
