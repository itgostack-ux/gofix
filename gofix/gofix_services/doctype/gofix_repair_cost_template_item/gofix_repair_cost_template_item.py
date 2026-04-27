from frappe.model.document import Document
from frappe.utils import flt


class GoFixRepairCostTemplateItem(Document):
    def before_save(self):
        self.total_cost = flt(self.standard_qty) * flt(self.unit_cost)
