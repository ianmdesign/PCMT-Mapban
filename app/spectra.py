from __future__ import annotations

import copy
from typing import Any

from .veto import build_turns, current_turn, slot_team


def _spectra_map(item: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    score = item.get("score")
    if not isinstance(score, list) or len(score) != 2:
        score = [None, None]
    result: dict[str, Any] = {
        "name": item["name"],
        "score": [score[0], score[1]],
    }
    if item.get("bannedByTeamId"):
        result["bannedBy"] = next(
            t["slot"] for t in session["teams"] if t["id"] == item["bannedByTeamId"]
        )
    if item.get("pickedByTeamId"):
        result["pickedBy"] = next(
            t["slot"] for t in session["teams"] if t["id"] == item["pickedByTeamId"]
        )
    if item.get("sidePickedByTeamId"):
        result["sidePickedBy"] = next(
            t["slot"] for t in session["teams"] if t["id"] == item["sidePickedByTeamId"]
        )
    # Spectra checks optional fields against `undefined`; omit unresolved values rather than sending null.
    if item.get("pickedAttack") is not None:
        result["pickedAttack"] = bool(item["pickedAttack"])
    return result



def _nobii_selector_plan(session: dict[str, Any]) -> dict[str, Any] | None:
    """Expose the full BO5 selector order as a harmless Spectra payload extension.

    Upstream Spectra ignores unknown fields. The adapted Nobii frontend already
    understands customFormatData.selectorTeam and can therefore render future
    team logos correctly before the corresponding maps are resolved.
    """
    if session.get("format") != "bo5" or not session.get("doubleBanAdvantageTeamId"):
        return None

    states: list[str] = []
    selectors: list[int] = []
    for turn in build_turns(session):
        if turn.kind == "ban":
            state = "ban"
        elif turn.kind in ("pick", "side_pick"):
            state = "pick"
        elif turn.kind == "side_ban":
            state = "ban"
        elif turn.kind == "final_side":
            state = "decider"
        else:
            continue
        states.append(state)
        selectors.append(int(turn.slot))

    return {
        "pickAmount": states.count("pick"),
        "banAmount": states.count("ban"),
        "hasDecider": "decider" in states,
        "pickBanStates": states,
        "selectorTeam": selectors,
        # The Nobii mapban strip currently does not consume sideSelectorTeam, but
        # its interface requires the field when customFormatData is present.
        "sideSelectorTeam": selectors.copy(),
    }

def spectra_view(session: dict[str, Any]) -> dict[str, Any]:
    teams = sorted(session["teams"], key=lambda item: item["slot"])

    # A premade/reset session exists so links can be distributed, but Spectra should
    # stay visually idle until the producer explicitly presses Start Veto.
    if session.get("setupRequired", False) or session.get("vetoStartedAt") is None:
        return {
            "sessionIdentifier": session["id"],
            "organizationName": session["organizationName"],
            "isSupporter": False,
            "teams": [
                {"name": team["name"], "tricode": team["tricode"], "url": team["logo"]}
                for team in teams
            ],
            "format": session["format"],
            "availableMaps": [],
            "selectedMaps": [],
            "stage": "decider",
            "actingTeamCode": teams[0]["tricode"],
            "actingTeam": 0,
        }

    turn = current_turn(session)
    map_clones = copy.deepcopy(session["veto"]["maps"])

    # While a side is pending, Spectra expects sidePickedBy to already identify the acting team.
    if turn and turn.get("sideMap"):
        side_map = next(item for item in map_clones if item["id"] == turn["sideMap"]["id"])
        side_map["sidePickedByTeamId"] = turn["teamId"]
        if turn["kind"] == "final_side" and side_map["status"] == "available":
            side_map["status"] = "decider"
            existing_orders = [m["order"] for m in map_clones if m["order"] is not None]
            side_map["order"] = (max(existing_orders) + 1) if existing_orders else 0

    selected = sorted(
        [item for item in map_clones if item["status"] != "available"],
        key=lambda item: int(item["order"] if item["order"] is not None else 999),
    )
    available = [item for item in map_clones if item["status"] == "available"]

    if turn:
        acting_team = int(turn["teamSlot"])
        acting_team_code = slot_team(session, acting_team)["tricode"]
        if turn.get("phase") == "side":
            stage = "side"
        elif turn.get("mapAction") == "ban":
            stage = "ban"
        elif turn.get("mapAction") == "pick":
            stage = "pick"
        else:
            stage = "decider"
    else:
        acting_team = 0
        acting_team_code = slot_team(session, 0)["tricode"]
        stage = "decider"

    payload = {
        "sessionIdentifier": session["id"],
        "organizationName": session["organizationName"],
        # Preserve upstream supporter/watermark behavior rather than bypassing it.
        "isSupporter": False,
        "teams": [
            {"name": team["name"], "tricode": team["tricode"], "url": team["logo"]}
            for team in teams
        ],
        "format": session["format"],
        "availableMaps": [_spectra_map(item, session) for item in available],
        "selectedMaps": [_spectra_map(item, session) for item in selected],
        "stage": stage,
        "actingTeamCode": acting_team_code,
        "actingTeam": acting_team,
    }

    selector_plan = _nobii_selector_plan(session)
    if selector_plan is not None:
        payload["customFormatData"] = selector_plan

    return payload
