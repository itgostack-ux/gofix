# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class WalkinSource(Document):
	def validate(self):
		# Ensure source name is trimmed
		if self.source_name:
			self.source_name = self.source_name.strip()
