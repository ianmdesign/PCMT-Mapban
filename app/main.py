from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import socketio
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import ConfigError, ConfigManager
from .db import Database
from .spectra import spectra_view
from .veto import (
    VetoError,
    apply_action,
    available_maps,
    current_turn,
    make_session_id,
    new_veto_state,
    reset_veto,
    slot_team,
    validate_session_pool,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

config_manager = ConfigManager()
db = Database()

app = FastAPI(title="Spectra Map Ban", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
application = socketio.ASGIApp(sio, other_asgi_app=app)

mutation_locks: dict[str, asyncio.Lock] = {}
cleanup_task: asyncio.Task | None = None


def lock_for(session_id: str) -> asyncio.Lock:
    if session_id not in mutation_locks:
        mutation_locks[session_id] = asyncio.Lock()
    return mutation_locks[session_id]


def now_ts() -> int:
    return int(time.time())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def derive_token(session_id: str, role: str, subject: str = "") -> str:
    secret = config_manager.loaded.app.secret_key.encode("utf-8")
    message = f"mapban:{session_id}:{role}:{subject}".encode("utf-8")
    return _b64url(hmac.new(secret, message, hashlib.sha256).digest())


def admin_token(session: dict[str, Any]) -> str:
    return derive_token(session["id"], "admin")


def team_token(session: dict[str, Any], team_id: str) -> str:
    return derive_token(session["id"], "team", team_id)


def identify_token(session: dict[str, Any], token: str) -> tuple[str, str | None] | None:
    if token and hmac.compare_digest(token, admin_token(session)):
        return ("admin", None)
    for team in session["teams"]:
        if token and hmac.compare_digest(token, team_token(session, team["id"])):
            return ("team", team["id"])
    return None


def require_not_expired(session: dict[str, Any]) -> None:
    if int(session["expiresAt"]) <= now_ts():
        raise HTTPException(status_code=410, detail="Session expired")


def normalize_session(session: dict[str, Any]) -> bool:
    """Apply backwards-compatible defaults to sessions created by older builds."""
    changed = False
    for item in session.get("veto", {}).get("maps", []):
        score = item.get("score")
        if not isinstance(score, list) or len(score) != 2:
            item["score"] = [None, None]
            changed = True

    # Older builds created a `pending` session with setupRequired=false and no
    # explicit Start button. Treat those as draft/configuring sessions now.
    if (
        session.get("status") == "pending"
        and session.get("vetoStartedAt") is None
        and not session.get("setupRequired", False)
    ):
        session["setupRequired"] = True
        session["status"] = "configuring"
        changed = True
    return changed


def get_session_or_404(session_id: str) -> dict[str, Any]:
    session = db.get_session(session_id.upper())
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    require_not_expired(session)
    if normalize_session(session):
        db.save_session(session)
    return session


def require_auth(session: dict[str, Any], authorization: str | None) -> tuple[str, str | None]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = authorization[7:].strip()
    identity = identify_token(session, token)
    if not identity:
        raise HTTPException(status_code=403, detail="Invalid session token")
    return identity


def require_admin(session: dict[str, Any], authorization: str | None) -> None:
    role, _ = require_auth(session, authorization)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")


def make_url(path: str) -> str:
    return f"{config_manager.loaded.app.public_base_url}{path}"


def overlay_url(session_id: str) -> str:
    base = config_manager.loaded.app.spectra_overlay_base_url
    if not base:
        return ""
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}sessionId={session_id}"


def session_links(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "adminUrl": make_url(
            f"/session/{session['id']}/admin/{admin_token(session)}"
        ),
        "teamUrls": [
            {
                "teamId": team["id"],
                "name": team["name"],
                "url": make_url(
                    f"/session/{session['id']}/team/{team_token(session, team['id'])}"
                ),
            }
            for team in sorted(session["teams"], key=lambda item: item["slot"])
        ],
        "spectraOverlayUrl": overlay_url(session["id"]),
    }


def app_view(
    session: dict[str, Any], *, role: str = "spectator", viewer_team_id: str | None = None
) -> dict[str, Any]:
    setup_required = bool(session.get("setupRequired", False))
    started = session.get("vetoStartedAt") is not None
    turn = current_turn(session) if (not setup_required and started) else None
    teams = sorted(session["teams"], key=lambda item: item["slot"])
    history = db.history(session["id"])
    result: dict[str, Any] = {
        "sessionId": session["id"],
        "organizationName": session["organizationName"],
        "createdAt": session["createdAt"],
        "expiresAt": session["expiresAt"],
        "status": session["status"],
        "vetoStartedAt": session.get("vetoStartedAt"),
        "setupRequired": setup_required,
        "format": session["format"],
        "doubleBanAdvantageTeamId": session.get("doubleBanAdvantageTeamId"),
        "teams": teams,
        "maps": session["veto"]["maps"],
        "mapPool": [{"id": m["id"], "name": m["name"]} for m in session["veto"]["maps"]],
        "currentTurn": turn,
        "availableMaps": available_maps(session),
        "history": history,
        "viewer": {"role": role, "teamId": viewer_team_id},
        "permissions": {
            "canSwapTeams": role == "admin" and setup_required and not started,
            "canChangeAdvantage": role == "admin"
            and setup_required
            and session["format"] == "bo5"
            and not started,
            "canStart": role == "admin" and setup_required and not started,
            "canAct": started
            and not setup_required
            and (
                role == "admin"
                or (
                    role == "team"
                    and turn is not None
                    and turn["teamId"] == viewer_team_id
                )
            ),
            "canUndo": role == "admin" and bool(history) and started and not setup_required,
            "canReset": role == "admin",
            "canEditScores": role == "admin" and started and not setup_required,
        },
    }
    if role == "admin":
        result["links"] = session_links(session)
    return result



class WebSocketHub:
    def __init__(self) -> None:
        self.connections: dict[str, list[dict[str, Any]]] = {}
        self.lock = asyncio.Lock()

    async def add(
        self, session_id: str, websocket: WebSocket, role: str, team_id: str | None
    ) -> None:
        async with self.lock:
            self.connections.setdefault(session_id, []).append(
                {"ws": websocket, "role": role, "teamId": team_id}
            )

    async def remove(self, session_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            entries = self.connections.get(session_id, [])
            self.connections[session_id] = [entry for entry in entries if entry["ws"] is not websocket]
            if not self.connections[session_id]:
                self.connections.pop(session_id, None)

    async def broadcast(self, session: dict[str, Any]) -> None:
        session_id = session["id"]
        async with self.lock:
            entries = list(self.connections.get(session_id, []))
        dead: list[WebSocket] = []
        for entry in entries:
            try:
                await entry["ws"].send_json(
                    {
                        "type": "session_state",
                        "data": app_view(
                            session,
                            role=entry["role"],
                            viewer_team_id=entry["teamId"],
                        ),
                    }
                )
            except Exception:
                dead.append(entry["ws"])
        for ws in dead:
            await self.remove(session_id, ws)


hub = WebSocketHub()


async def broadcast_session(session: dict[str, Any]) -> None:
    await hub.broadcast(session)
    await sio.emit("session_data", spectra_view(session), room=f"spectra:{session['id']}")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/session-options")
async def session_options() -> dict[str, Any]:
    try:
        config_manager.reload()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    cfg = config_manager.loaded
    return {
        "organizationName": cfg.app.organization_name,
        "teams": [team.__dict__ for team in cfg.teams],
        "maps": [item.__dict__ for item in cfg.maps],
        "defaultPool": list(cfg.default_pool),
        "allowManualTeams": cfg.app.allow_manual_teams,
        "formats": ["bo1", "bo3", "bo5"],
    }


def _make_session_team(raw: Any, slot: int) -> dict[str, Any]:
    cfg = config_manager.loaded
    if not isinstance(raw, dict):
        raise VetoError("Each team must be an object")
    source = raw.get("source")
    if source == "configured":
        team_id = str(raw.get("teamId", ""))
        match = next((team for team in cfg.teams if team.id == team_id), None)
        if not match:
            raise VetoError(f"Unknown configured team: {team_id}")
        name, tricode, logo = match.name, match.tricode, match.logo
        config_team_id = match.id
    elif source == "manual" and cfg.app.allow_manual_teams:
        name = str(raw.get("name", "")).strip()
        tricode = str(raw.get("tricode", "")).strip()
        logo = str(raw.get("logo", "")).strip()
        config_team_id = None
        if not name or not tricode:
            raise VetoError("Manual teams require a name and tricode")
        if len(name) > 80 or len(tricode) > 12 or len(logo) > 1000:
            raise VetoError("Manual team data is too long")
    else:
        raise VetoError("Invalid team source")

    return {
        "id": "team_" + secrets.token_urlsafe(9),
        "configTeamId": config_team_id,
        "name": name,
        "tricode": tricode,
        "logo": logo,
        "slot": slot,
    }


@app.post("/api/sessions")
async def create_session(request: Request) -> JSONResponse:
    try:
        config_manager.reload()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    cfg = config_manager.loaded
    raw = await request.json()
    try:
        if not isinstance(raw, dict):
            raise VetoError("Request body must be an object")
        raw_teams = raw.get("teams")
        if not isinstance(raw_teams, list) or len(raw_teams) != 2:
            raise VetoError("Exactly two teams are required")
        teams = [_make_session_team(raw_teams[0], 0), _make_session_team(raw_teams[1], 1)]
        if (
            teams[0].get("configTeamId")
            and teams[0].get("configTeamId") == teams[1].get("configTeamId")
        ):
            raise VetoError("The same configured team cannot occupy both slots")

        # Team selection creates the persistent session immediately. Format and map
        # pool are draft settings that the producer can autosave/reopen later.
        fmt = str(raw.get("format", "bo3")).lower()
        if fmt not in ("bo1", "bo3", "bo5"):
            fmt = "bo3"

        requested_pool = raw.get("mapPool")
        if isinstance(requested_pool, list):
            map_ids = [str(item) for item in requested_pool]
        else:
            map_ids = list(cfg.default_pool)
        known = {item.id: item for item in cfg.maps}
        if len(set(map_ids)) != len(map_ids):
            raise VetoError("Map pool cannot contain duplicate maps")
        unknown = set(map_ids) - set(known)
        if unknown:
            raise VetoError("Unknown map ids: " + ", ".join(sorted(unknown)))
        map_pool = [{"id": map_id, "name": known[map_id].name} for map_id in map_ids]

        session_id = ""
        for _ in range(20):
            candidate = make_session_id(cfg.app.session_id_length)
            if db.get_session(candidate) is None:
                session_id = candidate
                break
        if not session_id:
            raise VetoError("Unable to allocate a unique session id")

        created = now_ts()
        payload = {
            "id": session_id,
            "organizationName": cfg.app.organization_name,
            "createdAt": created,
            "expiresAt": created + (cfg.app.session_lifetime_hours * 60 * 60),
            "status": "configuring",
            "vetoStartedAt": None,
            "setupRequired": True,
            "format": fmt,
            "doubleBanAdvantageTeamId": None,
            "teams": teams,
            "veto": new_veto_state(map_pool),
        }
        db.create_session(session_id, payload)
    except VetoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    body = {
        "sessionId": session_id,
        "expiresAt": payload["expiresAt"],
        **session_links(payload),
    }
    return JSONResponse(body, status_code=201)


@app.get("/api/sessions/{session_id}/view")
async def get_session_view(session_id: str, token: str = "") -> dict[str, Any]:
    session = get_session_or_404(session_id)
    identity = identify_token(session, token)
    if not identity:
        raise HTTPException(status_code=403, detail="Invalid session token")
    return app_view(session, role=identity[0], viewer_team_id=identity[1])


@app.post("/api/sessions/{session_id}/swap")
async def swap_teams(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        require_admin(session, authorization)
        if not session.get("setupRequired", False) or session.get("vetoStartedAt") is not None:
            raise HTTPException(status_code=409, detail="Teams are locked after Start Veto")
        session["teams"][0]["slot"], session["teams"][1]["slot"] = (
            session["teams"][1]["slot"],
            session["teams"][0]["slot"],
        )
        db.save_session(session)
    await broadcast_session(session)
    return app_view(session, role="admin")


@app.post("/api/sessions/{session_id}/advantage")
async def set_advantage(
    session_id: str, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    raw = await request.json()
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        require_admin(session, authorization)
        if session["format"] != "bo5":
            raise HTTPException(status_code=409, detail="This is not a BO5 session")
        if not session.get("setupRequired", False) or session.get("vetoStartedAt") is not None:
            raise HTTPException(
                status_code=409, detail="Double-ban advantage is locked after Start Veto"
            )
        team_id = raw.get("teamId") if isinstance(raw, dict) else None
        if team_id is not None and team_id not in {team["id"] for team in session["teams"]}:
            raise HTTPException(status_code=400, detail="Unknown session team")
        session["doubleBanAdvantageTeamId"] = team_id
        db.save_session(session)
    await broadcast_session(session)
    return app_view(session, role="admin")


@app.post("/api/sessions/{session_id}/settings")
async def set_session_settings(
    session_id: str, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    """Autosave producer draft settings while the session is not started."""
    try:
        config_manager.reload()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    cfg = config_manager.loaded
    raw = await request.json()
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        require_admin(session, authorization)
        if not session.get("setupRequired", False) or session.get("vetoStartedAt") is not None:
            raise HTTPException(status_code=409, detail="Veto settings are locked after Start Veto")
        try:
            if not isinstance(raw, dict):
                raise VetoError("Request body must be an object")
            fmt = str(raw.get("format", session.get("format", "bo3"))).lower()
            if fmt not in ("bo1", "bo3", "bo5"):
                raise VetoError("Format must be bo1, bo3, or bo5")

            map_ids = raw.get("mapPool")
            if not isinstance(map_ids, list):
                raise VetoError("mapPool must be an array")
            map_ids = [str(item) for item in map_ids]
            if len(set(map_ids)) != len(map_ids):
                raise VetoError("Map pool cannot contain duplicate maps")
            known = {item.id: item for item in cfg.maps}
            unknown = set(map_ids) - set(known)
            if unknown:
                raise VetoError("Unknown map ids: " + ", ".join(sorted(unknown)))
            map_pool = [{"id": map_id, "name": known[map_id].name} for map_id in map_ids]

            advantage_team_id = raw.get("doubleBanAdvantageTeamId")
            team_ids = {team["id"] for team in session["teams"]}
            if fmt == "bo5":
                if advantage_team_id is not None and advantage_team_id not in team_ids:
                    raise VetoError("Unknown double-ban advantage team")
            else:
                advantage_team_id = None

            session["format"] = fmt
            session["doubleBanAdvantageTeamId"] = advantage_team_id
            session["veto"] = new_veto_state(map_pool)
            session["vetoStartedAt"] = None
            session["setupRequired"] = True
            session["status"] = "configuring"
            db.clear_actions(session["id"])
            db.save_session(session)
        except VetoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_session(session)
    return app_view(session, role="admin")


@app.post("/api/sessions/{session_id}/start")
async def start_session(
    session_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        require_admin(session, authorization)
        if not session.get("setupRequired", False) or session.get("vetoStartedAt") is not None:
            raise HTTPException(status_code=409, detail="Veto has already been started")
        try:
            config_manager.reload()
            known_ids = {item.id for item in config_manager.loaded.maps}
            map_ids = [str(item["id"]) for item in session["veto"]["maps"]]
            validate_session_pool(map_ids, known_ids)
        except (ConfigError, VetoError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session["setupRequired"] = False
        session["vetoStartedAt"] = now_ts()
        session["status"] = "active"
        db.save_session(session)
    await broadcast_session(session)
    return app_view(session, role="admin")


@app.post("/api/sessions/{session_id}/actions")
async def submit_action(
    session_id: str, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    raw = await request.json()
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        role, viewer_team_id = require_auth(session, authorization)
        if session.get("setupRequired", False) or session.get("vetoStartedAt") is None:
            raise HTTPException(
                status_code=409, detail="The producer has not started this veto"
            )
        turn = current_turn(session)
        if not turn:
            raise HTTPException(status_code=409, detail="Veto is already complete")
        acting_team_id = turn["teamId"] if role == "admin" else viewer_team_id
        if acting_team_id != turn["teamId"]:
            raise HTTPException(status_code=403, detail="It is not your turn")

        before = copy.deepcopy(session)
        try:
            updated, kind, summary = apply_action(
                session,
                acting_team_id=acting_team_id,
                map_id=raw.get("mapId") if isinstance(raw, dict) else None,
                side=raw.get("side") if isinstance(raw, dict) else None,
            )
        except VetoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        sequence = len(db.history(session["id"])) + 1
        db.save_session(updated)
        db.append_action(
            session_id=session["id"],
            sequence=sequence,
            actor_team_id=acting_team_id,
            submitted_by="admin" if role == "admin" else f"team:{acting_team_id}",
            action_kind=kind,
            summary=summary,
            before=before,
            after=updated,
        )
        session = updated
    await broadcast_session(session)
    return app_view(session, role=role, viewer_team_id=viewer_team_id)


@app.post("/api/sessions/{session_id}/scores")
async def set_scores(
    session_id: str, request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    raw = await request.json()
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        require_admin(session, authorization)
        if session.get("setupRequired", False) or session.get("vetoStartedAt") is None:
            raise HTTPException(status_code=409, detail="Start the veto before setting scores")
        updates = raw.get("scores") if isinstance(raw, dict) else None
        if not isinstance(updates, list):
            raise HTTPException(status_code=400, detail="scores must be an array")

        maps_by_id = {item["id"]: item for item in session["veto"]["maps"]}
        try:
            for update in updates:
                if not isinstance(update, dict):
                    raise VetoError("Each score update must be an object")
                map_id = str(update.get("mapId", ""))
                item = maps_by_id.get(map_id)
                if not item:
                    raise VetoError(f"Unknown map: {map_id}")
                if item.get("status") not in ("picked", "decider"):
                    raise VetoError(f"Scores can only be set for selected maps: {item['name']}")
                score = update.get("score")
                if not isinstance(score, list) or len(score) != 2:
                    raise VetoError(f"Score for {item['name']} must contain Team A and Team B values")
                parsed: list[int | None] = []
                for value in score:
                    if value is None or value == "":
                        parsed.append(None)
                        continue
                    if isinstance(value, bool):
                        raise VetoError("Score values must be whole numbers")
                    try:
                        number = int(value)
                    except (TypeError, ValueError) as exc:
                        raise VetoError("Score values must be whole numbers") from exc
                    if number < 0 or number > 99:
                        raise VetoError("Score values must be between 0 and 99")
                    parsed.append(number)
                if (parsed[0] is None) != (parsed[1] is None):
                    raise VetoError(f"Enter both scores for {item['name']}, or clear both")
                item["score"] = parsed
        except VetoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        db.save_session(session)
    await broadcast_session(session)
    return app_view(session, role="admin")


@app.post("/api/sessions/{session_id}/undo")
async def undo_action(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        require_admin(session, authorization)
        restored = db.pop_last_action(session["id"])
        if restored is None:
            raise HTTPException(status_code=409, detail="There is no action to undo")
        # Once the first ban has ever gone through, pre-veto settings stay locked.
        if session.get("vetoStartedAt") is not None:
            restored["vetoStartedAt"] = session["vetoStartedAt"]
            restored["status"] = "active"
        db.save_session(restored)
        session = restored
    await broadcast_session(session)
    return app_view(session, role="admin")


@app.post("/api/sessions/{session_id}/reset")
async def reset_session(session_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    async with lock_for(session_id.upper()):
        session = get_session_or_404(session_id)
        require_admin(session, authorization)
        updated = reset_veto(session)
        db.clear_actions(session["id"])
        db.save_session(updated)
        session = updated
    await broadcast_session(session)
    return app_view(session, role="admin")


@app.websocket("/ws/session/{session_id}")
async def session_websocket(websocket: WebSocket, session_id: str) -> None:
    token = websocket.query_params.get("token", "")
    session = db.get_session(session_id.upper())
    if not session or int(session["expiresAt"]) <= now_ts():
        await websocket.close(code=4404)
        return
    identity = identify_token(session, token)
    if not identity:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    await hub.add(session["id"], websocket, identity[0], identity[1])
    await websocket.send_json(
        {
            "type": "session_state",
            "data": app_view(session, role=identity[0], viewer_team_id=identity[1]),
        }
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove(session["id"], websocket)


@sio.event
async def connect(sid: str, environ: dict[str, Any], auth: Any = None) -> bool:
    return True


@sio.on("logon")
async def spectra_logon(sid: str, data: Any) -> None:
    try:
        if isinstance(data, str):
            data = json.loads(data)
        session_id = str((data or {}).get("sessionId", "")).upper()
    except Exception:
        session_id = ""
    session = db.get_session(session_id) if session_id else None
    if not session or int(session["expiresAt"]) <= now_ts():
        await sio.emit("logon_fail", {"message": "Invalid or expired session"}, to=sid)
        return
    await sio.enter_room(sid, f"spectra:{session_id}")
    await sio.emit("session_data", spectra_view(session), to=sid)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/session/{session_id}/admin/{token}")
async def admin_page(session_id: str, token: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/session/{session_id}/team/{token}")
async def team_page(session_id: str, token: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


async def _cleanup_loop() -> None:
    while True:
        try:
            db.delete_expired()
        except Exception:
            pass
        await asyncio.sleep(3600)


@app.on_event("startup")
async def startup() -> None:
    global cleanup_task
    db.delete_expired()
    cleanup_task = asyncio.create_task(_cleanup_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    global cleanup_task
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
