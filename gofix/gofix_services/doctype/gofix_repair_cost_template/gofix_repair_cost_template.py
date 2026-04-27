from frappe.model.document import Document
from frappe.utils import flt


class GoFixRepairCostTemplate(Document):
    def before_save(self):
        total_parts = sum(flt(r.total_cost) for r in self.items)
        self.estimated_parts_cost = total_parts
