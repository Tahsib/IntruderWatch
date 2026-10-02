import os
import unittest
from unittest.mock import MagicMock, patch

# Ensure required environment variables for alert_service exist
os.environ["TWILIO_ACCOUNT_SID"] = "ACmockaccountsid0000000000000000"
os.environ["TWILIO_AUTH_TOKEN"] = "mockauthtoken00000000000000000"
os.environ["TWILIO_PHONE_NUMBER"] = "+15551234567"
os.environ["ALERT_PHONE_NUMBERS"] = "+15559876543"

try:
    import alert_service.alert_service as alert_mod
except ModuleNotFoundError:
    import alert_service as alert_mod


class TestAlertServiceEdgeCases(unittest.TestCase):
    def test_enriched_alert_formatting_multiple_persons(self):
        """Edge Case: Multi-person detection formatted with urgent alert title and count."""
        with patch.object(alert_mod.http_session, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            alert_mod.send_ntfy_photo(
                camera_id=1,
                timestamp="2026-10-03 02:15:30.123456",
                filename="/app/captures/camera_1/2026-10-03/det_test.jpg",
                human_count=3,
                max_confidence=0.95,
            )

            mock_post.assert_called_once()
            headers = mock_post.call_args[1]["headers"]
            self.assertIn("3 Persons", headers["Title"])
            self.assertIn("3 persons detected (95% conf)", headers["Message"])
            self.assertEqual(headers["Priority"], "5")

    def test_single_person_alert_formatting(self):
        """Edge Case: Single person detection formatted cleanly."""
        with patch.object(alert_mod.http_session, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            alert_mod.send_ntfy_photo(
                camera_id=2,
                timestamp="2026-10-03 02:15:30.123456",
                filename="/app/captures/camera_2/2026-10-03/det_test.jpg",
                human_count=1,
                max_confidence=0.82,
            )

            headers = mock_post.call_args[1]["headers"]
            self.assertEqual(headers["Title"], "Intruder: Camera 2")
            self.assertIn("Person detected (82% conf)", headers["Message"])

    def test_legacy_payload_graceful_fallback(self):
        """Edge Case: Missing or zero confidence defaults gracefully without crash."""
        with patch.object(alert_mod.http_session, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            alert_mod.send_ntfy_photo(
                camera_id=3,
                timestamp="2026-10-03 02:15:30",
                filename="/app/captures/camera_3/2026-10-03/det_test.jpg",
                human_count=1,
                max_confidence=0.0,
            )

            headers = mock_post.call_args[1]["headers"]
            self.assertEqual(headers["Title"], "Intruder: Camera 3")
            self.assertIn("Person detected", headers["Message"])
            self.assertNotIn("% conf", headers["Message"])


if __name__ == "__main__":
    unittest.main()
