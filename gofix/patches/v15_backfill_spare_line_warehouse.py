import frappe


def execute():
	"""Record where already-reserved spares physically are.

	The ticket has always said a spare arrived and never said where. The field
	is new, so every existing line is blank; this fills the ones that can be
	derived, and leaves the rest blank rather than guessing.

	Two sources, both evidence rather than inference:
	  1. the Purchase Receipt that fulfilled the line's Material Request --
	     the warehouse it was actually booked into;
	  2. failing that, the ticket's own store, but only when that store really
	     holds stock of the item. A spare "reserved" at a counter with none of
	     it is not located there, and saying so would send a technician to an
	     empty shelf.
	"""
	if not frappe.db.has_column("SR Spare Line", "warehouse"):
		return

	rows = frappe.db.sql("""
		SELECT sl.name, sl.spare_item, sl.material_request, sr.source_warehouse
		  FROM `tabSR Spare Line` sl
		  JOIN `tabService Request` sr ON sr.name = sl.parent
		 WHERE sl.parenttype = 'Service Request'
		   AND IFNULL(sl.warehouse, '') = ''
		   AND sl.status IN ('Reserved', 'Issued', 'Consumed')
	""", as_dict=True)

	from_receipt = with_stock = 0
	for row in rows:
		warehouse = None

		if row.material_request:
			warehouse = frappe.db.sql_list("""
				SELECT pri.warehouse
				  FROM `tabPurchase Receipt Item` pri
				  JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent AND pr.docstatus = 1
				 WHERE pri.material_request = %(mr)s AND pri.item_code = %(item)s
				   AND IFNULL(pri.warehouse, '') != ''
				 ORDER BY pr.posting_date DESC LIMIT 1
			""", {"mr": row.material_request, "item": row.spare_item})
			warehouse = warehouse[0] if warehouse else None
			if warehouse:
				from_receipt += 1

		if not warehouse and row.source_warehouse:
			on_hand = frappe.db.get_value(
				"Bin",
				{"item_code": row.spare_item, "warehouse": row.source_warehouse},
				"actual_qty",
			)
			if on_hand and float(on_hand) > 0:
				warehouse = row.source_warehouse
				with_stock += 1

		if warehouse:
			frappe.db.set_value("SR Spare Line", row.name, "warehouse", warehouse,
			                    update_modified=False)

	frappe.db.commit()
	print(
		f"Spare line location backfilled: {from_receipt} from a receipt, "
		f"{with_stock} from the ticket's own store, "
		f"{len(rows) - from_receipt - with_stock} left blank (no evidence)"
	)
