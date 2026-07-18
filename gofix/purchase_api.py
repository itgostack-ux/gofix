"""GoFix purchase flow helpers — Gap 14: Auto-convert spare parts MR to POs."""

import frappe
from frappe import _
from frappe.utils import nowdate, add_days


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
        po = frappe.get_doc({
            "doctype": "Purchase Order",
            "company": mr.company,
            "supplier": supplier,
            "transaction_date": nowdate(),
            "schedule_date": add_days(nowdate(), 7),
            "set_warehouse": mr.set_warehouse or (items[0].get("warehouse") if items else None),
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
