import frappe
from frappe.model.document import Document


class TechnicianGrade(Document):
	def validate(self):
		seen = set()
		for row in self.skills or []:
			if row.issue_category in seen:
				frappe.throw(f"Duplicate issue category: {row.issue_category}")
			seen.add(row.issue_category)
