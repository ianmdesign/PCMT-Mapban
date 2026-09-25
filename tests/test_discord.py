from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from fastapi import HTTPException

from app.db import Database
from app.discord import DiscordPublishError, send_webhook, veto_embed, webhook_url
from app.veto import apply_action, available_maps, current_turn, new_veto_state


URL = "https://discord.com/api/webhooks/123/example-token?thread_id=456"
ROOT = Path(__file__).resolve().parents[1]
_import_data = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {"CONFIG_DIR": str(ROOT / "config"), "DATA_DIR": _import_data.name}):
    from app import main


def make_session(fmt: str = "bo3", *, complete: bool = True) -> dict:
    created = int(time.time())
    session = {
        "id": "TEST01",
        "organizationName": "PCMT",
        "createdAt": created,
        "expiresAt": created + 3600,
        "status": "active",
        "vetoStartedAt": created,
        "setupRequired": False,
        "format": fmt,
        "doubleBanAdvantageTeamId": None,
        "teams": [
            {"id": "alpha", "name": "Alpha", "tricode": "ALP", "logo": "", "slot": 0},
            {"id": "bravo", "name": "Bravo", "tricode": "BRV", "logo": "", "slot": 1},
        ],
        "veto": new_veto_state([{"id": f"m{i}", "name": f"Map {i}"} for i in range(1, 8)]),
    }
    if complete:
        while turn := current_turn(session):
            session, _, _ = apply_action(
                session,
                acting_team_id=turn["teamId"],
                map_id=available_maps(session)[0]["id"] if turn["phase"] == "map" else None,
                side="attack" if turn["phase"] == "side" else None,
            )
        for item in session["veto"]["maps"]:
            if item["status"] in ("picked", "decider"):
                item["score"] = [13, 9]
    return session


class DiscordEmbedTests(unittest.TestCase):
    def test_embed_shows_selected_maps_in_order_and_bans_without_scores(self) -> None:
        for fmt, expected_maps in (("bo1", 1), ("bo3", 3), ("bo5", 5)):
            with self.subTest(fmt=fmt):
                payload = veto_embed(make_session(fmt))
                fields = payload["embeds"][0]["fields"]
                self.assertEqual(len(fields), expected_maps + 1)
                self.assertTrue(fields[0]["name"].startswith("Map 1 · "))
                self.assertIn("Attack:", fields[0]["value"])
                self.assertIn("Decider", fields[expected_maps - 1]["value"])
                self.assertEqual(fields[-1]["name"], "Banned maps")
                self.assertIn("ALP ban", fields[-1]["value"])
                self.assertNotIn("score", json.dumps(payload).lower())
                self.assertNotIn("13 – 9", json.dumps(payload))
                self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_only_discord_https_webhook_urls_are_accepted(self) -> None:
        for value, allowed in ((URL, True), ("", False), ("http://discord.com/api/webhooks/123/token", False),
                               ("https://discord.com.evil.test/api/webhooks/123/token", False),
                               ("https://discord.com:1234/api/webhooks/123/token", False)):
            with self.subTest(value=value), patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": value}):
                self.assertEqual(bool(webhook_url()), allowed)

    def test_webhook_posts_json_and_requests_confirmation(self) -> None:
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, *_):
                return b'{"id":"posted-message"}'

        payload = veto_embed(make_session())
        with patch("app.discord.urlopen", return_value=Response()) as post:
            send_webhook(URL, payload)
        request = post.call_args.args[0]
        self.assertIn("thread_id=456", request.full_url)
        self.assertIn("wait=true", request.full_url)
        self.assertEqual(json.loads(request.data), payload)

    def test_webhook_failure_does_not_expose_the_webhook_token(self) -> None:
        with patch("app.discord.urlopen", side_effect=HTTPError(URL, 404, "missing", None, None)):
            with self.assertRaises(DiscordPublishError) as raised:
                send_webhook(URL, veto_embed(make_session()))
        self.assertIn("404", str(raised.exception))
        self.assertNotIn("example-token", str(raised.exception))


class DiscordPublishTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(self.temp.name)
        self.db_patch = patch.object(main, "db", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.webhook_patch = patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": URL})
        self.webhook_patch.start()
        self.addCleanup(self.webhook_patch.stop)
        self.send_patch = patch.object(main, "send_webhook")
        self.send_mock = self.send_patch.start()
        self.addCleanup(self.send_patch.stop)
        self.broadcast_patch = patch.object(main, "broadcast_session", new_callable=AsyncMock)
        self.broadcast_patch.start()
        self.addCleanup(self.broadcast_patch.stop)

        self.session = make_session()
        self.db.create_session(self.session["id"], self.session)
        self.admin_header = f"Bearer {main.admin_token(self.session)}"

    async def test_button_is_only_enabled_for_completed_admin_sessions_with_webhook(self) -> None:
        self.assertTrue(main.app_view(self.session, role="admin")["permissions"]["canPublishDiscord"])
        self.assertFalse(main.app_view(self.session, role="team", viewer_team_id="alpha")["permissions"]["discordWebhookEnabled"])
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": ""}):
            self.assertFalse(main.app_view(self.session, role="admin")["permissions"]["discordWebhookEnabled"])
            with self.assertRaises(HTTPException) as raised:
                await main.publish_discord("TEST01", authorization=self.admin_header)
            self.assertEqual(raised.exception.status_code, 503)
        self.send_mock.assert_not_called()

    async def test_requires_admin_and_completed_veto(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await main.publish_discord("TEST01", authorization=f"Bearer {main.team_token(self.session, 'alpha')}")
        self.assertEqual(raised.exception.status_code, 403)

        session = make_session(complete=False)
        self.db.save_session(session)
        with self.assertRaises(HTTPException) as raised:
            await main.publish_discord("TEST01", authorization=self.admin_header)
        self.assertEqual(raised.exception.status_code, 409)
        self.send_mock.assert_not_called()

    async def test_success_is_persisted_and_second_click_does_not_send_again(self) -> None:
        view = await main.publish_discord("TEST01", authorization=self.admin_header)
        self.send_mock.assert_called_once()
        self.assertFalse(view["permissions"]["canPublishDiscord"])
        self.assertTrue(view["discordPublishedAt"])
        self.assertEqual(self.db.get_session("TEST01")["discordPublishedAt"], view["discordPublishedAt"])

        with self.assertRaises(HTTPException) as raised:
            await main.publish_discord("TEST01", authorization=self.admin_header)
        self.assertEqual(raised.exception.status_code, 409)
        self.send_mock.assert_called_once()

        reset = await main.reset_session("TEST01", authorization=self.admin_header)
        self.assertIsNone(reset["discordPublishedAt"])

    async def test_failed_publish_can_be_retried(self) -> None:
        self.send_mock.side_effect = DiscordPublishError("Discord rejected the message (HTTP 404)")
        with self.assertRaises(HTTPException) as raised:
            await main.publish_discord("TEST01", authorization=self.admin_header)
        self.assertEqual(raised.exception.status_code, 502)
        self.assertNotIn("discordPublishedAt", self.db.get_session("TEST01"))

        self.send_mock.side_effect = None
        view = await main.publish_discord("TEST01", authorization=self.admin_header)
        self.assertTrue(view["discordPublishedAt"])
        self.assertEqual(self.send_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
