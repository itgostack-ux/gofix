"""Sync imported CH Customer Address rows into ERPNext Address."""

import frappe


def execute():
	if not frappe.db.table_exists("CH Customer Address"):
		return

	from gofix.gofix_services.customer_address import sync_customer_addresses

	customer_names = frappe.db.sql_list(
		"""
		SELECT DISTINCT a.parent
		  FROM `tabCH Customer Address` a
		  JOIN `tabCustomer` c ON c.name = a.parent
		  LEFT JOIN `tabDynamic Link` dl
		    ON dl.parenttype = 'Address'
		   AND dl.parent = c.customer_primary_address
		   AND dl.link_doctype = 'Customer'
		   AND dl.link_name = c.name
		 WHERE a.parenttype = 'Customer'
		   AND a.parentfield = 'billing_addresses'
		   AND (
			   IFNULL(a.is_active, 0) = 0
			OR IFNULL(c.customer_primary_address, '') = ''
			OR dl.name IS NULL
		   )
		 ORDER BY a.parent
		"""
	)
	summary = sync_customer_addresses(customer_names=customer_names, commit=True)
	frappe.logger("gofix").info("Customer address sync patch complete: %s", summary)
	print(f"Customer address sync patch complete: {summary}")
