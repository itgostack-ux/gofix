import frappe


def execute():
	"""Fill device_category / device_brand / device_model on existing tickets.

	These three became mandatory so a repair is reportable even when the device
	is not one we sell. Existing tickets predate the fields and would fail their
	next save -- including submitted ones a technician still has to update.

	Everything is derived from the device Item already on the ticket, which is
	where the new fields get their values from anyway; nothing is invented. A
	ticket whose Item declares none of the three is left alone rather than
	guessed at.
	"""
	if not frappe.db.has_column("Service Request", "device_category"):
		return

	# Brand is read through CH Model as well as the Item: some catalogue Items
	# carry a model but no brand of their own, and the model already names the
	# brand it belongs to. That is derivation from an existing record, not a
	# guess.
	rows = frappe.db.sql("""
		SELECT sr.name, i.ch_category, COALESCE(NULLIF(i.brand, ''), m.brand) AS brand,
		       COALESCE(NULLIF(i.ch_model, ''), '') AS ch_model
		FROM `tabService Request` sr
		JOIN `tabItem` i ON i.name = sr.device_item
		LEFT JOIN `tabCH Model` m ON m.name = i.ch_model
		WHERE IFNULL(sr.device_item, '') != ''
		  AND (IFNULL(sr.device_category, '') = ''
		    OR IFNULL(sr.device_brand, '') = ''
		    OR IFNULL(sr.device_model, '') = '')
	""", as_dict=True)

	filled = 0
	for row in rows:
		patch = {}
		if row.ch_category:
			patch["device_category"] = row.ch_category
		if row.brand:
			patch["device_brand"] = row.brand
		if row.ch_model:
			patch["device_model"] = row.ch_model
		if not patch:
			continue
		# update_modified=False: this is a schema backfill, not somebody editing
		# the ticket, and the modified stamp drives the ops queues.
		frappe.db.set_value("Service Request", row.name, patch, update_modified=False)
		filled += 1

	frappe.db.commit()
	print(f"Device taxonomy backfilled on {filled} of {len(rows)} Service Requests")
