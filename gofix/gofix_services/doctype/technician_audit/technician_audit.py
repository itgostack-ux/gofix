# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TechnicianAudit(Document):
	def before_save(self):
		"""Calculate time duration"""
		if self.assignment_from_time and self.assignment_to_time:
			from_time = frappe.utils.get_datetime(self.assignment_from_time)
			to_time = frappe.utils.get_datetime(self.assignment_to_time)
			
			# Calculate duration in minutes
			duration_minutes = frappe.utils.time_diff_in_seconds(to_time, from_time) / 60
			
			# Convert to human readable format
			if duration_minutes < 60:
				self.time_duration = f"{int(duration_minutes)} minutes"
			elif duration_minutes < 1440:  # Less than 24 hours
				hours = int(duration_minutes / 60)
				minutes = int(duration_minutes % 60)
				self.time_duration = f"{hours}h {minutes}m"
			else:  # Days
				days = int(duration_minutes / 1440)
				hours = int((duration_minutes % 1440) / 60)
				self.time_duration = f"{days}d {hours}h"
