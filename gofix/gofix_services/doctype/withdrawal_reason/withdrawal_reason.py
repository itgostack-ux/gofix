# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class WithdrawalReason(Document):
	def validate(self):
		# Ensure reason name is trimmed
		if self.reason_name:
			self.reason_name = self.reason_name.strip()
