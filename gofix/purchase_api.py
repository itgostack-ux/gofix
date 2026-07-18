"""GoFix purchase flow helpers — Gap 14: Auto-convert spare parts MR to POs."""

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate


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
			se.flags.ignore_permissions = True
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
					"status": "Awaiting Procurement",
					"spare_item": ("in", list(received_items)),
				},
				fields=["name", "spare_item", "item_name", "qty"],
			)
			if not lines:
				continue
			for line in lines:
				frappe.db.set_value(
					"SR Spare Line", line.name, "status", "Reserved", update_modified=False
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


@frappe.whitelist()
def create_pos_from_material_request(material_request: str) -> dict:
    """Gap 14: Convert a spare-parts Material Request into POs grouped by default supplier.

    For each item in the MR, looks up the Item Supplier table for the default supplier.
    Creates one PO per supplier, adds all their items, and returns the list of PO names.
    Returns an error message for items with no supplier configured.
    """
    frappe.has_permission("Purchase Order", "write", throw=True)

    mr = frappe.get_doc("Material Request", material_request)
    if mr.docstatus != 1:
        frappe.throw(_("Material Request must be submitted before converting to POs."))
    if mr.material_request_type != "Purchase":
        frappe.throw(_("Only Purchase type Material Requests can be converted to POs."))

    # Group items by supplier
    supplier_items: dict[str, list] = {}
    no_supplier = []

    for item in mr.items:
        pending_qty = (item.qty or 0) - (item.ordered_qty or 0)
        if pending_qty <= 0:
            continue

        supplier = frappe.db.get_value(
            "Item Supplier",
            {"parent": item.item_code, "parenttype": "Item"},
            "supplier",
            order_by="idx asc",
        )
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
            "rate": frappe.db.get_value("Item Price", {"item_code": item.item_code,
                "price_list": "Standard Buying", "selling": 0}, "price_list_rate") or 0,
            "warehouse": item.warehouse or mr.set_warehouse,
            "schedule_date": add_days(nowdate(), 7),
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
            "schedule_date": add_days(nowdate(), 7),
            "set_warehouse": target_wh,
            "custom_target_store": _store_for_warehouse(target_wh),
            "custom_purchase_type": "Taxable",  # standard GST purchase of repair spares
            "items": items,
        })
        po.insert(ignore_permissions=True)
        created_pos.append(po.name)

    result = {"created": created_pos}
    if no_supplier:
        result["warning"] = _("No default supplier configured for: {0}. "
                              "Set a supplier in the Item Supplier table for these items.").format(
            ", ".join(no_supplier)
        )
    return result
