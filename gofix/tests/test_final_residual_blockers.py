from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest import TestCase

from gofix import tracking
from gofix.gofix_services.page.service_hub import service_hub_api


class TestFinalResidualBlockers(TestCase):
	def test_service_hub_requires_app_dashboard_and_doctype_access(self):
		source = inspect.getsource(service_hub_api._check_hub_access)
		self.assertIn('"app_access_roles"', source)
		self.assertIn('"service_dashboard_roles"', source)
		self.assertIn('has_permission("Service Request"', source)

	def test_service_hub_scope_is_fail_closed_and_parameterized(self):
		source = inspect.getsource(service_hub_api._build_filters)
		self.assertIn("get_user_service_scope", source)
		self.assertIn('co = " AND 1=0"', source)
		self.assertIn('wh = " AND 1=0"', source)
		self.assertNotIn("frappe.db.escape", source)

	def test_tracking_mutation_is_post_only_and_all_lookups_are_throttled(self):
		source = inspect.getsource(tracking.customer_estimate_action)
		self.assertIn('methods=["POST"]', source)
		self.assertIn("rate_limit", source)
		self.assertIn("_check_public_lookup_rate", inspect.getsource(tracking._get_by_token))
		self.assertIn("_check_public_lookup_rate", inspect.getsource(tracking._get_by_phone))

	def test_dashboard_role_setting_exists(self):
		path = Path(service_hub_api.__file__).parents[2] / "doctype/gofix_settings/gofix_settings.json"
		settings = json.loads(path.read_text())
		self.assertIn("service_dashboard_roles", {row["fieldname"] for row in settings["fields"]})
