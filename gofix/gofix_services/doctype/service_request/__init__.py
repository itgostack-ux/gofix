# Copyright (c) 2025, GoFix and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ServiceRequest(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		actual_cost: DF.Currency | None
		amended_from: DF.Link | None
		assigned_technician: DF.Link | None
		brand: DF.Data | None
		complaint_description: DF.Text
		contact_number: DF.Data
		customer: DF.Link
		customer_name: DF.Data | None
		email: DF.Data | None
		estimated_cost: DF.Currency | None
		expected_completion_date: DF.Date | None
		item: DF.Link
		item_name: DF.Data | None
		priority: DF.Literal["Low", "Medium", "High", "Urgent"]
		remarks: DF.Text | None
		serial_no: DF.Link | None
		service_date: DF.Date
		service_invoice: DF.Link | None
		status: DF.Literal["Open", "In Progress", "Completed", "Cancelled"]
	# end: auto-generated types

	pass
