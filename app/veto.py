from __future__ import annotations

import copy
import secrets
import time
from dataclasses import dataclass
from typing import Any


class VetoError(ValueError):
    pass


@dataclass(frozen=True)
class Turn:
    kind: str
    slot: int


BASE_TURNS: dict[str, tuple[Turn, ...]] = {
    "bo1": (
        Turn("ban", 0),
        Turn("ban", 1),
        Turn("ban", 0),
        Turn("ban", 1),
        Turn("ban", 0),
        Turn("ban", 1),
        Turn("final_side", 0),
    ),
    "bo3": (
        Turn("ban", 0),
        Turn("ban", 1),
        Turn("pick", 0),
        Turn("side_pick", 1),
        Turn("side_ban", 0),
        Turn("ban", 1),
        Turn("final_side", 0),
    ),
    "bo5": (
        Turn("ban", 0),
        Turn("ban", 1),
        Turn("pick", 0),
        Turn("side_pick", 1),
        Turn("side_pick", 0),
        Turn("side_pick", 1),
        Turn("final_side", 0),
    ),
}


SESSION_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_session_id(length: int) -> str:
    return "".join(secrets.choice(SESSION_ALPHABET) for _ in range(length))


def new_veto_state(map_pool: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "currentTurn": 0,
        "maps": [
            {
                "id": item["id"],
                "name": item["name"],
                "status": "available",
                "order": None,
                "bannedByTeamId": None,
                "pickedByTeamId": None,
                "sidePickedByTeamId": None,
                "pickedAttack": None,
                "score": [None, None],
            }
            for item in map_pool
        ],
    }


def slot_team(session: dict[str, Any], slot: int) -> dict[str, Any]:
    for team in session["teams"]:
        if team["slot"] == slot:
            return team
    raise VetoError(f"No team assigned to slot {slot}")


def team_slot(session: dict[str, Any], team_id: str) -> int:
    for team in session["teams"]:
        if team["id"] == team_id:
            return int(team["slot"])
    raise VetoError("Unknown team")


def build_turns(session: dict[str, Any]) -> list[Turn]:
    fmt = session["format"]
    if fmt not in BASE_TURNS:
        raise VetoError("Unsupported format")
    turns = list(BASE_TURNS[fmt])
    if fmt == "bo5" and session.get("doubleBanAdvantageTeamId"):
        advantage_slot = team_slot(session, session["doubleBanAdvantageTeamId"])
        turns[0] = Turn("ban", advantage_slot)
        turns[1] = Turn("ban", advantage_slot)
    return turns


def _latest_picked_map(session: dict[str, Any]) -> dict[str, Any]:
    picked = [
        item
        for item in session["veto"]["maps"]
        if item["status"] == "picked" and item.get("order") is not None
    ]
    if not picked:
        raise VetoError("Unable to determine map awaiting side selection")
    return max(picked, key=lambda item: int(item["order"]))


def current_turn(session: dict[str, Any]) -> dict[str, Any] | None:
    turns = build_turns(session)
    index = int(session["veto"]["currentTurn"])
    if index >= len(turns):
        return None
    turn = turns[index]
    team = slot_team(session, turn.slot)
    side_map = None
    map_action = None
    phase = "map"

    if turn.kind in ("side_pick", "side_ban"):
        previous_pick = _latest_picked_map(session)
        if previous_pick.get("pickedAttack") is None:
            # Side choice is its own submitted action. Only after it is saved do we
            # expose the map pick/ban portion of this same team's turn.
            side_map = previous_pick
            phase = "side"
        else:
            phase = "map"
            map_action = "pick" if turn.kind == "side_pick" else "ban"
    elif turn.kind == "final_side":
        available = [item for item in session["veto"]["maps"] if item["status"] == "available"]
        if len(available) != 1:
            raise VetoError("Final side turn requires exactly one remaining map")
        side_map = available[0]
        phase = "side"
    elif turn.kind == "ban":
        map_action = "ban"
    elif turn.kind == "pick":
        map_action = "pick"

    return {
        "index": index,
        "kind": turn.kind,
        "phase": phase,
        "teamId": team["id"],
        "teamSlot": turn.slot,
        "teamName": team["name"],
        "sideMap": ({"id": side_map["id"], "name": side_map["name"]} if side_map else None),
        "mapAction": map_action,
    }


def available_maps(session: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"id": item["id"], "name": item["name"]}
        for item in session["veto"]["maps"]
        if item["status"] == "available"
    ]


def _find_map(session: dict[str, Any], map_id: str) -> dict[str, Any]:
    for item in session["veto"]["maps"]:
        if item["id"] == map_id:
            return item
    raise VetoError("Unknown map")


def _next_order(session: dict[str, Any]) -> int:
    resolved = [item for item in session["veto"]["maps"] if item["order"] is not None]
    return len(resolved)


def apply_action(
    session: dict[str, Any],
    *,
    acting_team_id: str,
    map_id: str | None,
    side: str | None,
) -> tuple[dict[str, Any], str, str]:
    turn = current_turn(session)
    if not turn:
        raise VetoError("Veto is already complete")
    if turn["teamId"] != acting_team_id:
        raise VetoError("It is not this team's turn")

    if side is not None:
        side = side.lower().strip()
    if side not in (None, "attack", "defense"):
        raise VetoError("Side must be attack or defense")

    phase = turn["phase"]
    needs_side = phase == "side"
    needs_map = phase == "map" and turn["mapAction"] is not None
    if needs_side and side is None:
        raise VetoError("A side selection is required")
    if not needs_side and side is not None:
        raise VetoError("Choose the side before moving to the map action")
    if needs_map and not map_id:
        raise VetoError("A map selection is required")
    if not needs_map and map_id:
        raise VetoError("This step does not accept a map selection")

    updated = copy.deepcopy(session)
    turn = current_turn(updated)
    actor = next(team for team in updated["teams"] if team["id"] == acting_team_id)
    actor_name = actor["name"]

    if needs_side:
        side_map_id = turn["sideMap"]["id"]
        side_map = _find_map(updated, side_map_id)
        if turn["kind"] == "final_side" and side_map["status"] == "available":
            side_map["status"] = "decider"
            side_map["order"] = _next_order(updated)
        side_map["sidePickedByTeamId"] = acting_team_id
        side_map["pickedAttack"] = side == "attack"
        summary = f"{actor_name} chose {side.title()} on {side_map['name']}"
        action_kind = "side"

        # Combined BO3/BO5 turns deliberately stay on the same turn after the
        # side is saved. current_turn() will then expose the next pick/ban phase.
        if turn["kind"] == "final_side":
            updated["veto"]["currentTurn"] += 1
    else:
        target = _find_map(updated, map_id)
        if target["status"] != "available":
            raise VetoError("That map is no longer available")
        target["order"] = _next_order(updated)
        if turn["mapAction"] == "ban":
            target["status"] = "banned"
            target["bannedByTeamId"] = acting_team_id
            summary = f"{actor_name} banned {target['name']}"
            action_kind = "ban"
        else:
            target["status"] = "picked"
            target["pickedByTeamId"] = acting_team_id
            summary = f"{actor_name} picked {target['name']}"
            action_kind = "pick"
        updated["veto"]["currentTurn"] += 1

    if updated.get("vetoStartedAt") is None:
        updated["vetoStartedAt"] = int(time.time())
        updated["status"] = "active"

    if current_turn(updated) is None:
        updated["status"] = "complete"

    return updated, action_kind, summary


def reset_veto(session: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(session)
    pool = [{"id": item["id"], "name": item["name"]} for item in session["veto"]["maps"]]
    updated["veto"] = new_veto_state(pool)
    # Reset is an intentional full restart. Keep the same teams/session links, but
    # reopen pre-veto configuration and block team actions until the producer saves it.
    updated["vetoStartedAt"] = None
    updated["setupRequired"] = True
    updated["status"] = "configuring"
    updated.pop("discordPublishedAt", None)
    return updated


def validate_session_pool(map_ids: list[str], known_map_ids: set[str]) -> None:
    if len(map_ids) != 7 or len(set(map_ids)) != 7:
        raise VetoError("Exactly 7 unique maps must be selected")
    unknown = set(map_ids) - known_map_ids
    if unknown:
        raise VetoError("Unknown map ids: " + ", ".join(sorted(unknown)))
