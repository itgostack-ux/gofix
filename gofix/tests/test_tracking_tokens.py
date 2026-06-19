import uuid
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gofix import tracking


class TestTrackingTokens(unittest.TestCase):
	def test_make_tracking_token_returns_random_guid(self):
		frappe_stub = SimpleNamespace(db=SimpleNamespace(exists=lambda *_args, **_kwargs: False))
		with patch.object(tracking, "frappe", frappe_stub):
			token = tracking.make_tracking_token()
		self.assertEqual(str(uuid.UUID(token, version=4)), token)

	@patch("gofix.tracking._clear_lookup_failures")
	@patch("gofix.tracking._check_lookup_lockout")
	@patch("gofix.tracking._tracking_column_exists", return_value=True)
	@patch("gofix.tracking.frappe.get_all", return_value=["SR-260619-0001"])
	@patch("gofix.tracking._build_tracking_data", return_value={"name": "SR-260619-0001"})
	def test_get_by_token_uses_stored_token_lookup(
		self,
		_build_tracking_data,
		get_all,
		_column_exists,
		_check_lookup_lockout,
		_clear_lookup_failures,
	):
		data = tracking._get_by_token("  8fbcf413-3db5-4961-acd1-99c84386f2cf  ")

		self.assertEqual(data, {"name": "SR-260619-0001"})
		get_all.assert_called_once()
		self.assertEqual(
			get_all.call_args.kwargs["filters"],
			{
				"tracking_token": "8fbcf413-3db5-4961-acd1-99c84386f2cf",
				"status": ["not in", ["Cancelled"]],
			},
		)
		self.assertEqual(get_all.call_args.kwargs["pluck"], "name")
		self.assertEqual(get_all.call_args.kwargs["limit"], 1)


if __name__ == "__main__":
	unittest.main()
