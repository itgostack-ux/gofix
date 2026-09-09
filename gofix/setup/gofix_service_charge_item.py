"""The bench fee every repair carries.

A repair is never only parts and labour: the device is logged, tested, cleaned,
handled and handed back, and that work belonged to no line on the estimate. It
was absorbed silently, which made small jobs look free to run and left the
counter explaining a total that did not add up from what was listed.

So it is a line like any other -- a real Item, priced on the same price list the
spares use, appearing last on every estimate. Being an Item rather than a
hardcoded number means finance sees it as revenue in its own right, it carries
its own tax template, and the amount is changed in the master by whoever owns
pricing rather than in code.
"""

import frappe

SERVICE_CHARGE_ITEM = "GOFIX-SERVICE-CHARGE"
SERVICE_CHARGE_RATE = 200.0
PRICE_LIST = "CH POS"
# India Compliance makes HSN/SAC mandatory on every Item. 998716 is
# "Maintenance and repair services of telecommunication equipments", which is
# what this charge is and what the 46 other GoFix service items already carry --
# so the bench fee is taxed the same way the repair it belongs to is.
SAC_CODE = "998716"
# ch_item_master governs every Item on this bench: an Item without a CH Sub
# Category of the right nature is refused activation. "Mobile Repair Labour" is
# nature Service, gofix category Repair, and carries an income account -- which
# is precisely what a bench fee is, so the charge lands in repair revenue rather
# than in a category of its own.
SUB_CATEGORY = "Repair Services-Mobile Repair Labour"


def create_gofix_service_charge_item():
	group = ("Services" if frappe.db.exists("Item Group", "Services")
	         else frappe.db.get_value("Item Group", {"is_group": 0}, "name"))
	sub_category = SUB_CATEGORY if frappe.db.exists("CH Sub Category", SUB_CATEGORY) else None
	if not sub_category:
		# Governance would refuse the Item anyway; say why rather than letting
		# the whole migrate fail on a validation message.
		frappe.log_error(
			"CH Sub Category %s is missing, so the GoFix service charge item "
			"cannot be created." % SUB_CATEGORY, "gofix_service_charge_item")
		return
	uom = "Nos" if frappe.db.exists("UOM", "Nos") else frappe.db.get_value("UOM", {}, "name")

	if not frappe.db.exists("Item", SERVICE_CHARGE_ITEM):
		frappe.get_doc({
			"doctype": "Item",
			"item_code": SERVICE_CHARGE_ITEM,
			"item_name": "GoFix Service Charges",
			"description": "Standard bench and handling charge applied to every repair.",
			"item_group": group,
			"stock_uom": uom,
			# Not stock: there is nothing to count, and making it stock would
			# put a phantom quantity into every store's bin.
			"is_stock_item": 0,
			"is_sales_item": 1,
			"is_purchase_item": 0,
			"include_item_in_manufacturing": 0,
			# The category is the sub category's own parent: read it rather than
			# naming it twice and letting the two drift apart.
			"ch_category": frappe.db.get_value("CH Sub Category", sub_category, "category"),
			"ch_sub_category": sub_category,
			**({"gst_hsn_code": SAC_CODE}
			   if frappe.db.exists("GST HSN Code", SAC_CODE) else {}),
		}).insert(ignore_permissions=True)

	# Priced on the same list the spares are quoted from, so one price list
	# drives the whole estimate.
	if frappe.db.exists("Price List", PRICE_LIST):
		existing = frappe.db.get_value(
			"Item Price",
			{"item_code": SERVICE_CHARGE_ITEM, "price_list": PRICE_LIST},
			"name",
		)
		if not existing:
			frappe.get_doc({
				"doctype": "Item Price",
				"item_code": SERVICE_CHARGE_ITEM,
				"price_list": PRICE_LIST,
				"price_list_rate": SERVICE_CHARGE_RATE,
			}).insert(ignore_permissions=True)


def get_service_charge() -> dict:
	"""The charge as the estimate should show it: item, name and current rate.

	Read from the price list every time rather than cached in code, so changing
	the master changes what is quoted.
	"""
	if not frappe.db.exists("Item", SERVICE_CHARGE_ITEM):
		return {}
	rate = frappe.db.get_value(
		"Item Price",
		{"item_code": SERVICE_CHARGE_ITEM, "price_list": PRICE_LIST},
		"price_list_rate",
	)
	return {
		"item_code": SERVICE_CHARGE_ITEM,
		"item_name": frappe.db.get_value("Item", SERVICE_CHARGE_ITEM, "item_name"),
		"rate": float(rate or 0),
	}
