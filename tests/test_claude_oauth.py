from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tokenstatus.readers import claude_oauth


def _credential(*, expired: bool) -> dict:
    now = time.time() * 1000
    return {
        "claudeAiOauth": {
            "accessToken": "access-old",
            "refreshToken": "refresh-old",
            "expiresAt": now - 1_000 if expired else now + 3_600_000,
            "refreshTokenExpiresAt": now + 86_400_000,
            "scopes": ["user:inference", "user:profile"],
            "subscriptionType": "team",
        },
        "unrelated": {"preserved": True},
    }


def _response(status: int, body: dict) -> Mock:
    result = Mock()
    result.status_code = status
    result.json.return_value = body
    return result


class ClaudeOauthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / ".credentials.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, value: dict) -> None:
        self.path.write_text(json.dumps(value), encoding="utf-8")

    @patch("tokenstatus.readers.claude_oauth.credentials_path")
    @patch("tokenstatus.readers.claude_oauth.requests.get")
    def test_valid_access_token_fetches_usage_without_refresh(self, get, path) -> None:
        self._write(_credential(expired=False))
        path.return_value = self.path
        get.return_value = _response(200, {
            "five_hour": {"utilization": 12, "resets_at": None},
            "seven_day": {"utilization": 34, "resets_at": None},
        })

        with patch("tokenstatus.readers.claude_oauth.requests.post") as post:
            usage, err = claude_oauth.fetch_oauth_usage("profile")

        self.assertEqual(err, "")
        self.assertEqual(usage.five_hour_pct, 12)
        self.assertEqual(usage.seven_day_pct, 34)
        post.assert_not_called()

    @patch("tokenstatus.readers.claude_oauth.credentials_path")
    @patch("tokenstatus.readers.claude_oauth.requests.get")
    @patch("tokenstatus.readers.claude_oauth.requests.post")
    def test_expired_access_token_is_refreshed_and_preserves_file(self, post, get, path) -> None:
        self._write(_credential(expired=True))
        path.return_value = self.path
        post.return_value = _response(200, {
            "access_token": "access-new",
            "refresh_token": "refresh-new",
            "expires_in": 3600,
            "refresh_token_expires_in": 2_592_000,
            "scope": "user:inference user:profile",
        })
        get.return_value = _response(200, {
            "five_hour": {"utilization": 56, "resets_at": None},
            "seven_day": {"utilization": 78, "resets_at": None},
        })

        usage, err = claude_oauth.fetch_oauth_usage("profile")

        self.assertEqual(err, "")
        self.assertEqual(usage.five_hour_pct, 56)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["claudeAiOauth"]["accessToken"], "access-new")
        self.assertEqual(saved["claudeAiOauth"]["refreshToken"], "refresh-new")
        self.assertTrue(saved["unrelated"]["preserved"])

    @patch("tokenstatus.readers.claude_oauth.credentials_path")
    @patch("tokenstatus.readers.claude_oauth.requests.get")
    @patch("tokenstatus.readers.claude_oauth.requests.post")
    def test_invalid_refresh_returns_login_error_without_corrupting_file(self, post, get, path) -> None:
        original = _credential(expired=True)
        self._write(original)
        path.return_value = self.path
        post.return_value = _response(400, {"error": "invalid_grant"})

        usage, err = claude_oauth.fetch_oauth_usage("profile")

        self.assertIsNone(usage)
        self.assertIn("다시 로그인", err)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), original)
        get.assert_not_called()

    @patch("tokenstatus.readers.claude_reader.load_session_key")
    @patch("tokenstatus.readers.claude_reader.fetch_oauth_usage")
    def test_reader_prefers_profile_oauth_over_legacy_web_key(self, oauth, key) -> None:
        from tokenstatus.readers.claude_api import ApiUsage
        from tokenstatus.readers.claude_reader import read_claude

        oauth.return_value = (ApiUsage(
            five_hour_pct=11,
            five_hour_resets_at=None,
            seven_day_pct=22,
            seven_day_resets_at=None,
            seven_day_sonnet_pct=None,
            seven_day_opus_pct=None,
            org_id="",
        ), "")

        snapshot = read_claude(
            str(self.root), 300, 100,
            use_api=True,
            account_id="company",
            claude_config_dir=str(self.root),
        )

        self.assertEqual(snapshot.percent, 11)
        self.assertIn("Claude Code OAuth", snapshot.note)
        key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
