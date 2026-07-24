import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class GoFixSettings(Document):
	def validate(self):
		if not (
			flt(self.technician_recommendation_high_score)
			>= flt(self.technician_recommendation_score)
			>= flt(self.technician_available_score)
		):
			frappe.throw(_("Technician recommendation scores must be ordered high ≥ recommended ≥ available."))
