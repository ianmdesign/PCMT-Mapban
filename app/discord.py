from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class DiscordPublishError(RuntimeError):
    pass


def webhook_url() -> str:
    """Return only a configured Discord webhook; never expose it to the UI."""
    value = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and bool(re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+/?", parsed.path))
        )
    except ValueError:
        return ""
    if not valid:
        return ""
    return value


def _label(value: Any, limit: int = 100) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def veto_embed(session: dict[str, Any]) -> dict[str, Any]:
    teams = sorted(session["teams"], key=lambda team: team["slot"])
    by_id = {team["id"]: team for team in teams}
    maps = sorted(session["veto"]["maps"], key=lambda item: item["order"])
    selected = [item for item in maps if item["status"] in ("picked", "decider")]
    banned = [item for item in maps if item["status"] == "banned"]

    fields: list[dict[str, Any]] = []
    for index, item in enumerate(selected, 1):
        picker = by_id.get(item.get("pickedByTeamId"))
        side_picker = by_id.get(item.get("sidePickedByTeamId"))
        other = next((team for team in teams if team is not side_picker), None)
        lines = [
            "Decider" if item["status"] == "decider" else f"Picked by {_label(picker['name'], 80)}"
        ]
        if side_picker and other and item.get("pickedAttack") is not None:
            attacker = side_picker if item["pickedAttack"] else other
            defender = other if item["pickedAttack"] else side_picker
            lines.append(
                f"Attack: {_label(attacker['tricode'], 12)} · Defense: {_label(defender['tricode'], 12)}"
            )
        fields.append(
            {
                "name": f"Map {index} · {_label(item['name'], 180)}",
                "value": "\n".join(lines),
                "inline": False,
            }
        )

    if banned:
        bans = [
            f"{_label(item['name'], 100)} — {_label(by_id[item['bannedByTeamId']]['tricode'], 12)} ban"
            for item in banned
        ]
        fields.append({"name": "Banned maps", "value": "\n".join(bans)[:1024], "inline": False})

    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": "Map veto complete",
                "description": (
                    f"**{_label(teams[0]['name'], 80)}** vs **{_label(teams[1]['name'], 80)}**"
                    f"\nBest of {session['format'][2:]}"
                ),
                "color": 0x378DF2,
                "fields": fields,
                "footer": {"text": f"{_label(session['organizationName'], 120)} · {session['id']}"},
            }
        ],
    }


def send_webhook(url: str, payload: dict[str, Any]) -> None:
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "wait"]
    query.append(("wait", "true"))
    target = urlunsplit(parsed._replace(query=urlencode(query)))
    request = Request(
        target,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "PCMT-Mapban/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            message = json.load(response) if response.status == 200 else None
            if not isinstance(message, dict) or not message.get("id"):
                raise DiscordPublishError("Discord did not confirm the message")
    except HTTPError as exc:
        raise DiscordPublishError(f"Discord rejected the message (HTTP {exc.code})") from exc
    except (URLError, OSError, ValueError) as exc:
        raise DiscordPublishError("Could not confirm the Discord message; check the webhook") from exc
