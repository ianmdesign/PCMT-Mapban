from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class TeamConfig:
    id: str
    name: str
    tricode: str
    logo: str


@dataclass(frozen=True)
class MapConfig:
    id: str
    name: str


@dataclass(frozen=True)
class AppConfig:
    organization_name: str
    public_base_url: str
    spectra_overlay_base_url: str
    session_lifetime_hours: int
    session_id_length: int
    allow_manual_teams: bool
    secret_key: str


@dataclass(frozen=True)
class LoadedConfig:
    app: AppConfig
    teams: tuple[TeamConfig, ...]
    maps: tuple[MapConfig, ...]
    default_pool: tuple[str, ...]


class ConfigManager:
    def __init__(self, config_dir: str | Path | None = None):
        self.config_dir = Path(config_dir or os.getenv("CONFIG_DIR", "/app/config"))
        self.loaded = self._load()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except FileNotFoundError as exc:
            raise ConfigError(f"Missing configuration file: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ConfigError(f"Top-level JSON value in {path} must be an object")
        return value

    @staticmethod
    def _require_string(obj: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
        value = obj.get(key)
        if not isinstance(value, str):
            raise ConfigError(f"{key} must be a string")
        value = value.strip()
        if not allow_empty and not value:
            raise ConfigError(f"{key} cannot be empty")
        return value

    def _load(self) -> LoadedConfig:
        app_raw = self._read_json(self.config_dir / "config.json")
        teams_raw = self._read_json(self.config_dir / "teams.json")
        maps_raw = self._read_json(self.config_dir / "maps.json")

        app = AppConfig(
            organization_name=self._require_string(app_raw, "organizationName"),
            public_base_url=self._require_string(app_raw, "publicBaseUrl").rstrip("/"),
            spectra_overlay_base_url=self._require_string(
                app_raw, "spectraOverlayBaseUrl", allow_empty=True
            ).rstrip("/"),
            session_lifetime_hours=int(app_raw.get("sessionLifetimeHours", 48)),
            session_id_length=int(app_raw.get("sessionIdLength", 6)),
            allow_manual_teams=bool(app_raw.get("allowManualTeams", True)),
            secret_key=self._require_string(app_raw, "secretKey"),
        )
        if app.session_lifetime_hours <= 0 or app.session_lifetime_hours > 24 * 30:
            raise ConfigError("sessionLifetimeHours must be between 1 and 720")
        if app.session_id_length < 6 or app.session_id_length > 16:
            raise ConfigError("sessionIdLength must be between 6 and 16")
        if len(app.secret_key) < 24:
            raise ConfigError("secretKey must be at least 24 characters")

        raw_teams = teams_raw.get("teams")
        if not isinstance(raw_teams, list):
            raise ConfigError("teams.json must contain a teams array")
        teams: list[TeamConfig] = []
        team_ids: set[str] = set()
        for index, raw in enumerate(raw_teams):
            if not isinstance(raw, dict):
                raise ConfigError(f"teams[{index}] must be an object")
            team = TeamConfig(
                id=self._require_string(raw, "id"),
                name=self._require_string(raw, "name"),
                tricode=self._require_string(raw, "tricode"),
                logo=self._require_string(raw, "logo", allow_empty=True),
            )
            if team.id in team_ids:
                raise ConfigError(f"Duplicate team id: {team.id}")
            team_ids.add(team.id)
            teams.append(team)

        raw_maps = maps_raw.get("maps")
        raw_default = maps_raw.get("defaultPool")
        if not isinstance(raw_maps, list):
            raise ConfigError("maps.json must contain a maps array")
        if not isinstance(raw_default, list):
            raise ConfigError("maps.json must contain a defaultPool array")
        maps: list[MapConfig] = []
        map_ids: set[str] = set()
        for index, raw in enumerate(raw_maps):
            if not isinstance(raw, dict):
                raise ConfigError(f"maps[{index}] must be an object")
            item = MapConfig(
                id=self._require_string(raw, "id"),
                name=self._require_string(raw, "name"),
            )
            if item.id in map_ids:
                raise ConfigError(f"Duplicate map id: {item.id}")
            map_ids.add(item.id)
            maps.append(item)

        default_pool = tuple(str(item) for item in raw_default)
        if len(default_pool) != 7 or len(set(default_pool)) != 7:
            raise ConfigError("defaultPool must contain exactly 7 unique map ids")
        unknown_defaults = set(default_pool) - map_ids
        if unknown_defaults:
            raise ConfigError(
                "defaultPool references unknown map ids: " + ", ".join(sorted(unknown_defaults))
            )

        return LoadedConfig(
            app=app,
            teams=tuple(teams),
            maps=tuple(maps),
            default_pool=default_pool,
        )

    def reload(self) -> None:
        self.loaded = self._load()
