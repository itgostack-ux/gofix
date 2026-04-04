import frappe
from frappe.model.document import Document


class SolutionSpareMapping(Document):
	def validate(self):
		# Ensure no duplicate mapping of same item to same solution
		existing = frappe.db.exists("Solution Spare Mapping", {
			"repair_solution": self.repair_solution,
			"spare_item": self.spare_item,
			"name": ["!=", self.name],
		})
		if existing:
			frappe.throw(
				f"Spare {self.spare_item} is already mapped to solution {self.repair_solution}"
			)
