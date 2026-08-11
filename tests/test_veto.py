from __future__ import annotations

import unittest

from app.veto import apply_action, build_turns, current_turn, new_veto_state, reset_veto
from app.spectra import spectra_view


def make_session(fmt: str, advantage: int | None = None):
    teams = [
        {"id": "alpha", "name": "Alpha", "tricode": "ALP", "logo": "", "slot": 0},
        {"id": "bravo", "name": "Bravo", "tricode": "BRV", "logo": "", "slot": 1},
    ]
    return {
        "id": "TEST01",
        "organizationName": "Test",
        "createdAt": 1,
        "expiresAt": 9999999999,
        "status": "active",
        "vetoStartedAt": 1234567890,
        "setupRequired": False,
        "format": fmt,
        "doubleBanAdvantageTeamId": None if advantage is None else teams[advantage]["id"],
        "teams": teams,
        "veto": new_veto_state([{"id": f"m{i}", "name": f"Map{i}"} for i in range(1, 8)]),
    }


def run_action(session, *, map_id=None, side=None):
    turn = current_turn(session)
    updated, _, _ = apply_action(
        session, acting_team_id=turn["teamId"], map_id=map_id, side=side
    )
    return updated


class VetoTests(unittest.TestCase):
    def test_bo1_sequence(self):
        session = make_session("bo1")
        self.assertEqual(
            [(t.kind, t.slot) for t in build_turns(session)],
            [("ban", 0), ("ban", 1), ("ban", 0), ("ban", 1), ("ban", 0), ("ban", 1), ("final_side", 0)],
        )
        for i in range(1, 7):
            session = run_action(session, map_id=f"m{i}")
        session = run_action(session, side="defense")
        self.assertEqual(session["status"], "complete")
        decider = next(m for m in session["veto"]["maps"] if m["status"] == "decider")
        self.assertEqual(decider["id"], "m7")
        self.assertFalse(decider["pickedAttack"])

    def test_bo3_sequence_and_split_side_then_map_turns(self):
        session = make_session("bo3")
        self.assertEqual(
            [(t.kind, t.slot) for t in build_turns(session)],
            [("ban", 0), ("ban", 1), ("pick", 0), ("side_pick", 1), ("side_ban", 0), ("ban", 1), ("final_side", 0)],
        )
        session = run_action(session, map_id="m1")
        session = run_action(session, map_id="m2")
        session = run_action(session, map_id="m3")
        self.assertEqual(current_turn(session)["phase"], "side")
        session = run_action(session, side="attack")
        self.assertEqual(current_turn(session)["phase"], "map")
        self.assertEqual(current_turn(session)["mapAction"], "pick")
        session = run_action(session, map_id="m4")
        self.assertEqual(current_turn(session)["phase"], "side")
        session = run_action(session, side="defense")
        self.assertEqual(current_turn(session)["phase"], "map")
        self.assertEqual(current_turn(session)["mapAction"], "ban")
        session = run_action(session, map_id="m5")
        session = run_action(session, map_id="m6")
        session = run_action(session, side="attack")
        self.assertEqual(session["status"], "complete")

    def test_bo5_double_ban_advantage_follows_team_after_swap(self):
        session = make_session("bo5", advantage=0)
        self.assertEqual([t.slot for t in build_turns(session)[:2]], [0, 0])
        session["teams"][0]["slot"], session["teams"][1]["slot"] = 1, 0
        self.assertEqual([t.slot for t in build_turns(session)[:2]], [1, 1])
        self.assertEqual(current_turn(session)["teamId"], "alpha")

    def test_spectra_exposes_bo5_double_ban_selector_plan(self):
        team_a_advantage = spectra_view(make_session("bo5", advantage=0))
        self.assertEqual(
            team_a_advantage["customFormatData"]["selectorTeam"],
            [0, 0, 0, 1, 0, 1, 0],
        )
        self.assertEqual(
            team_a_advantage["customFormatData"]["pickBanStates"],
            ["ban", "ban", "pick", "pick", "pick", "pick", "decider"],
        )

        team_b_advantage = spectra_view(make_session("bo5", advantage=1))
        self.assertEqual(
            team_b_advantage["customFormatData"]["selectorTeam"],
            [1, 1, 0, 1, 0, 1, 0],
        )

    def test_bo5_complete(self):
        session = make_session("bo5", advantage=1)
        session = run_action(session, map_id="m1")
        session = run_action(session, map_id="m2")
        session = run_action(session, map_id="m3")
        session = run_action(session, side="defense")
        session = run_action(session, map_id="m4")
        session = run_action(session, side="attack")
        session = run_action(session, map_id="m5")
        session = run_action(session, side="defense")
        session = run_action(session, map_id="m6")
        session = run_action(session, side="attack")
        self.assertEqual(session["status"], "complete")


    def test_reset_reopens_setup_and_unlocks_pre_veto_settings(self):
        session = make_session("bo5", advantage=0)
        session = run_action(session, map_id="m1")
        self.assertIsNotNone(session["vetoStartedAt"])
        reset = reset_veto(session)
        self.assertEqual(reset["status"], "configuring")
        self.assertTrue(reset["setupRequired"])
        self.assertIsNone(reset["vetoStartedAt"])
        self.assertEqual(reset["veto"]["currentTurn"], 0)
        self.assertTrue(all(item["status"] == "available" for item in reset["veto"]["maps"]))

    def test_spectra_side_pending_shape(self):
        session = make_session("bo3")
        session = run_action(session, map_id="m1")
        session = run_action(session, map_id="m2")
        session = run_action(session, map_id="m3")
        payload = spectra_view(session)
        self.assertEqual(payload["stage"], "side")
        picked = payload["selectedMaps"][-1]
        self.assertEqual(picked["name"], "Map3")
        self.assertEqual(picked["pickedBy"], 0)
        self.assertEqual(picked["sidePickedBy"], 1)
        self.assertNotIn("pickedAttack", picked)

    def test_spectra_switches_to_map_phase_after_side_selection(self):
        session = make_session("bo3")
        session = run_action(session, map_id="m1")
        session = run_action(session, map_id="m2")
        session = run_action(session, map_id="m3")
        session = run_action(session, side="attack")
        turn = current_turn(session)
        self.assertEqual(turn["phase"], "map")
        self.assertEqual(turn["mapAction"], "pick")
        payload = spectra_view(session)
        self.assertEqual(payload["stage"], "pick")
        picked = payload["selectedMaps"][-1]
        self.assertTrue(picked["pickedAttack"])

    def test_spectra_final_decider_is_selected_before_side_confirmation(self):
        session = make_session("bo1")
        for i in range(1, 7):
            session = run_action(session, map_id=f"m{i}")
        payload = spectra_view(session)
        self.assertEqual(payload["stage"], "side")
        self.assertEqual(payload["availableMaps"], [])
        self.assertEqual(len(payload["selectedMaps"]), 7)
        decider = payload["selectedMaps"][-1]
        self.assertEqual(decider["name"], "Map7")
        self.assertEqual(decider["sidePickedBy"], 0)
        self.assertNotIn("pickedAttack", decider)

    def test_spectra_is_idle_until_start(self):
        session = make_session("bo3")
        session["vetoStartedAt"] = None
        session["setupRequired"] = True
        session["status"] = "configuring"
        payload = spectra_view(session)
        self.assertEqual(payload["availableMaps"], [])
        self.assertEqual(payload["selectedMaps"], [])
        self.assertEqual(payload["stage"], "decider")

    def test_new_maps_start_with_blank_scores(self):
        session = make_session("bo3")
        self.assertTrue(all(item.get("score") == [None, None] for item in session["veto"]["maps"]))

    def test_spectra_forwards_saved_map_score(self):
        session = make_session("bo3")
        session = run_action(session, map_id="m1")
        session = run_action(session, map_id="m2")
        session = run_action(session, map_id="m3")
        picked = next(item for item in session["veto"]["maps"] if item["id"] == "m3")
        picked["score"] = [13, 9]
        payload = spectra_view(session)
        selected = next(item for item in payload["selectedMaps"] if item["name"] == "Map3")
        self.assertEqual(selected["score"], [13, 9])


if __name__ == "__main__":
    unittest.main()
