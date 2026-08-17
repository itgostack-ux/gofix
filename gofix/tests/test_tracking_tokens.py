import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gofix import tracking


class TestTrackingTokens(unittest.TestCase):
	def test_make_tracking_salt_is_random_hex(self):
		salt = tracking.make_tracking_salt()
		self.assertEqual(len(salt), 32)
		int(salt, 16)  # raises if not hex
		self.assertNotEqual(salt, tracking.make_tracking_salt())

	@patch.object(tracking, "_site_tracking_key", return_value=b"test-site-key")
	def test_derive_tracking_token_is_deterministic(self, _key):
		first = tracking.derive_tracking_token("SR-260722-0001", "abc123")
		second = tracking.derive_tracking_token("SR-260722-0001", "abc123")
		self.assertEqual(first, second)
		self.assertEqual(len(first), 64)
		int(first, 16)  # hex token

	@patch.object(tracking, "_site_tracking_key", return_value=b"test-site-key")
	def test_derive_tracking_token_rotates_with_salt(self, _key):
		base = tracking.derive_tracking_token("SR-260722-0001", "salt-one")
		rotated = tracking.derive_tracking_token("SR-260722-0001", "salt-two")
		other_doc = tracking.derive_tracking_token("SR-260722-0002", "salt-one")
		self.assertNotEqual(base, rotated)
		self.assertNotEqual(base, other_doc)

	@patch.object(tracking, "_site_tracking_key", return_value=b"test-site-key")
	def test_derive_tracking_token_requires_name_and_salt(self, _key):
		self.assertEqual(tracking.derive_tracking_token("SR-260722-0001", ""), "")
		self.assertEqual(tracking.derive_tracking_token("", "salt"), "")
		self.assertEqual(tracking.derive_tracking_token("SR-260722-0001", None), "")

	def test_ensure_tracking_token_reuses_stored_salt(self):
		stored = {"tracking_token": "", "tracking_token_salt": "feedbeef" * 4}
		set_values = []

		def fake_set_value(doctype, name, field, value=None, update_modified=True):
			set_values.append((field, value))

		frappe_stub = SimpleNamespace(
			db=SimpleNamespace(
				get_value=lambda *a, **k: SimpleNamespace(get=stored.get),
				set_value=fake_set_value,
				exists=lambda *a, **k: False,
				has_column=lambda *a, **k: True,
			),
			throw=tracking.frappe.throw,
		)
		with (
			patch.object(tracking, "frappe", frappe_stub),
			patch.object(tracking, "_site_tracking_key", return_value=b"test-site-key"),
		):
			first = tracking.ensure_tracking_token("SR-260722-0001")
			second = tracking.ensure_tracking_token("SR-260722-0001")

		self.assertEqual(first, second)
		self.assertEqual(
			first,
			tracking.hmac.new(
				b"test-site-key", b"SR-260722-0001:" + ("feedbeef" * 4).encode(), tracking.hashlib.sha256
			).hexdigest(),
		)
		# digest healed once, then stable — never a new token per call
		digest = tracking.tracking_token_digest(first)
		self.assertEqual(set_values[0], (tracking.TRACKING_TOKEN_FIELD, digest))
		stored["tracking_token"] = digest

	@patch("gofix.tracking._clear_lookup_failures")
	@patch("gofix.tracking._check_lookup_lockout")
	@patch("gofix.tracking._check_public_lookup_rate")
	@patch("gofix.tracking._tracking_column_exists", return_value=True)
	@patch("gofix.tracking.frappe.get_all", return_value=["SR-260619-0001"])
	@patch("gofix.tracking._build_tracking_data", return_value={"name": "SR-260619-0001"})
	def test_get_by_token_uses_stored_digest_lookup(
		self,
		_build_tracking_data,
		get_all,
		_column_exists,
		_check_public_lookup_rate,
		_check_lookup_lockout,
		_clear_lookup_failures,
	):
		data = tracking._get_by_token("  8fbcf413-3db5-4961-acd1-99c84386f2cf  ")

		self.assertEqual(data, {"name": "SR-260619-0001"})
		service_calls = [
			call for call in get_all.call_args_list
			if call.args and call.args[0] == "Service Request"
		]
		self.assertEqual(len(service_calls), 1)
		service_call = service_calls[0]
		self.assertEqual(
			service_call.kwargs["filters"],
			{
				"tracking_token": tracking.tracking_token_digest(
					"8fbcf413-3db5-4961-acd1-99c84386f2cf"
				),
				"decision": ["not in", ["Cancelled"]],
			},
		)
		self.assertEqual(service_call.kwargs["pluck"], "name")
		self.assertEqual(service_call.kwargs["limit"], 1)


if __name__ == "__main__":
	unittest.main()
