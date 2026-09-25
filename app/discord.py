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


def veto_embed(session: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    teams = sorted(session["teams"], key=lambda team: team["slot"])
    actions = sorted(history, key=lambda item: item["sequence"])
    maps = sorted(
        (item for item in session["veto"]["maps"] if item["order"] is not None),
        key=lambda item: item["order"],
    )
    picked_maps = iter(item for item in maps if item["status"] == "picked")
    decider = next((item for item in maps if item["status"] == "decider"), None)
    current_pick = None
    fields: list[dict[str, Any]] = []
    for index, item in enumerate(actions, 1):
        kind = item["action_kind"]
        label = {"ban": "Ban", "pick": "Pick", "side": "Starting side"}.get(kind, "Veto action")
        if index == len(actions) and kind == "side":
            label = "Decider side"
        if kind == "pick":
            current_pick = next(picked_maps, None)
        value = _label(item["summary"], 1024)
        if kind == "side":
            side_map = decider if label == "Decider side" else current_pick
            if side_map:
                side_picker = next(
                    (team for team in teams if team["id"] == side_map.get("sidePickedByTeamId")),
                    None,
                )
                other = next((team for team in teams if team is not side_picker), None)
                if side_picker and other and side_map.get("pickedAttack") is not None:
                    attacker = side_picker if side_map["pickedAttack"] else other
                    defender = other if side_map["pickedAttack"] else side_picker
                    value += (
                        f"\nAttack: {_label(attacker['tricode'], 12)}"
                        f" · Defense: {_label(defender['tricode'], 12)}"
                    )
        fields.append(
            {
                "name": f"{index} · {label}",
                "value": value[:1024],
                "inline": False,
            }
        )

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
