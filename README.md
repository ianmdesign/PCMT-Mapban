# Spectra Map Ban (self-hosted)

Standalone map-ban service designed to feed the existing ValoSpectra frontend while keeping session creation and veto control outside the Spectra Client.

The repository is laid out to work like the other Spectra Docker repositories: normal pushes/PRs run validation, and publishing a GitHub Release builds and pushes multi-architecture Docker images to GitHub Container Registry (GHCR).

## Repository layout

```text
.
├── .github/
│   └── workflows/
│       ├── build-and-publish.yml
│       └── test-and-build.yml
├── app/
│   ├── static/
│   ├── config.py
│   ├── db.py
│   ├── main.py
│   ├── spectra.py
│   └── veto.py
├── config/
│   └── .gitkeep
├── config.example/
│   ├── config.json
│   ├── maps.json
│   └── teams.json
├── data/
│   └── .gitkeep
├── tests/
│   └── test_veto.py
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

`config.example/` is safe starter configuration committed to Git. The live `config/` directory is deliberately ignored except for `.gitkeep`, because `config.json` contains secrets and the deployment-specific team/map configuration should not be writable by the web application.

## Included behavior

- Docker / Docker Compose deployment on port `5300`.
- Read-only persistent configuration from three separate JSON files:
  - `config/config.json` — service settings only.
  - `config/teams.json` — saved team dropdown only.
  - `config/maps.json` — known maps and the seven-map default pool only.
- No web/API endpoint can write those configuration files.
- Session creation is a two-step flow: Team A/B selection first, then the persistent session opens on Format + Map Pool.
- Team A and Team B can be selected from `teams.json` or manually entered for a single session.
- Session/team/overlay links already exist on the Format + Map Pool screen, so sessions can be prepared and distributed ahead of time.
- Format, BO5 advantage, Team A/B order, and map-pool changes autosave immediately to SQLite while the session is being configured.
- Map pool defaults from `maps.json`; intermediate draft pools may contain any number of configured maps, but exactly seven are required to press **Start Veto**.
- BO1, BO3 and BO5 fixed veto rules.
- BO5 optional double-map-ban advantage.
- **Start Veto** is the lock point: Team A/B order, format, map pool, and BO5 advantage lock and team actions become enabled.
- BO5 advantage follows the selected team identity when A/B are swapped before Start.
- Team links remain attached to the team identity when A/B are swapped.
- **Reset Veto** is the only way to unlock setup again; it clears veto history and map scores, returns the producer to Format + Map Pool, and retains the same teams/session links/expiry.
- Producer can act for the team whose turn it is, undo the last action, reset/reconfigure the veto, and set Team A/Team B scores on picked/decider maps.
- Sessions persist in SQLite for 48 hours by default and survive container restarts.
- Native WebSocket updates for the producer/team UI.
- Socket.IO `logon { sessionId }` / `session_data` compatibility for the Spectra map-ban overlay.

## Veto rules

### BO1

1. Team A ban
2. Team B ban
3. Team A ban
4. Team B ban
5. Team A ban
6. Team B ban
7. Team A chooses side on the remaining map

### BO3

1. Team A ban
2. Team B ban
3. Team A selects Map 1
4. Team B chooses side on Map 1 and selects Map 2
5. Team A chooses side on Map 2 and bans a map
6. Team B bans a map
7. Team A chooses side on the remaining Map 3

### BO5

1. Team A ban
2. Team B ban
3. Team A selects Map 1
4. Team B chooses side on Map 1 and selects Map 2
5. Team A chooses side on Map 2 and selects Map 3
6. Team B chooses side on Map 3 and selects Map 4
7. Team A chooses side on the remaining Map 5

With double-map-ban advantage, steps 1 and 2 both belong to the selected team. The rest of the BO5 order is unchanged.

## First repository setup

Create a new GitHub repository and copy this folder into the repository root. The workflow paths are already in the expected `.github/workflows/` location.

No registry username or repository name is hardcoded into the release workflow. When a release is published, the workflow derives the package name from `GITHUB_REPOSITORY` and lowercases it. For example, a repository named:

```text
ExampleUser/spectra-mapban
```

publishes:

```text
ghcr.io/exampleuser/spectra-mapban:latest
ghcr.io/exampleuser/spectra-mapban:v1.0.0
```

The release tag is used exactly as published.

The repository workflow uses the built-in `GITHUB_TOKEN`, so no Docker registry password is required. GitHub Actions needs `packages: write`, which is declared in the workflow.

## GitHub Actions

### Test and Build

`.github/workflows/test-and-build.yml` runs on pushes and pull requests targeting `main` and performs:

1. Python 3.12 setup.
2. Dependency installation.
3. Python compile check.
4. Veto/Spectra compatibility unit tests.
5. Validation of all three example JSON configuration files.
6. A Docker image build.

### Build and Push Docker Image on Release

`.github/workflows/build-and-publish.yml` mirrors the current Spectra release pattern:

1. Trigger on a **published GitHub Release**.
2. Check out the repository.
3. Set up QEMU.
4. Set up Docker Buildx.
5. Authenticate to `ghcr.io` with `GITHUB_TOKEN`.
6. Build for both `linux/amd64` and `linux/arm64`.
7. Push both `latest` and the GitHub release tag.

Example: publishing GitHub Release `v1.2.0` from `ExampleUser/spectra-mapban` creates:

```text
ghcr.io/exampleuser/spectra-mapban:latest
ghcr.io/exampleuser/spectra-mapban:v1.2.0
```

## Deployment configuration

Create the live configuration directory from the tracked examples:

```bash
mkdir -p config data
cp config.example/config.json config/config.json
cp config.example/teams.json config/teams.json
cp config.example/maps.json config/maps.json
```

Edit `config/config.json` before starting the service. Change `secretKey` before exposing the service.

Set `publicBaseUrl` to the external URL used for the map-ban service, for example:

```text
https://mapban.example.com
```

Optionally set `spectraOverlayBaseUrl` to the full Spectra frontend map-ban overlay route. Leave it empty if you only want the session ID copied.

Replace the sample teams in `config/teams.json` and update `config/maps.json` with every map the producer may select and exactly seven IDs in `defaultPool`.

`teams.json` and `maps.json` are deliberately independent files. The app only receives sanitized read-only copies from `/api/session-options`.

### Important logo note

For Spectra compatibility, team `logo` values should normally be absolute HTTP(S) URLs because the Spectra frontend may run on a different hostname from this service.

## Run a local build

The Compose file includes a local build definition, matching the general Spectra repository pattern:

```bash
docker compose up -d --build
```

With no `MAPBAN_IMAGE` environment variable, the locally built image is named `spectra-mapban:local`.

Open:

```text
http://localhost:5300
```

## Run the published GHCR image

Copy `.env.example` to `.env` and change `MAPBAN_IMAGE` to the package generated for your repository:

```bash
cp .env.example .env
```

For example:

```text
MAPBAN_IMAGE=ghcr.io/exampleuser/spectra-mapban:latest
```

Then deploy/update with:

```bash
docker compose pull
docker compose up -d
```

Because the Compose service has both `image:` and `build:`, use `docker compose up -d --build` when intentionally building locally. Normal release deployments can pull the GHCR image.

### Optional Discord map veto post

Set `DISCORD_WEBHOOK_URL` as an environment variable in the mapban container. With Docker Compose, put it in the local `.env` file alongside `MAPBAN_IMAGE`:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN
```

The URL stays on the server. Once a veto is complete, the admin page shows **Publish to Discord** only when a valid Discord webhook URL is configured. Clicking it sends one embed with the matchup and every ban, pick, and starting-side choice in draft order; scores are omitted. A successful post is recorded with the session so an accidental second click cannot send another copy. If the producer undoes the final veto action or resets the veto, the next completed veto can be posted again. An empty or invalid URL hides the button.

## Persistent data

`./data` is mounted to `/data` and contains `mapban.sqlite3`. Sessions have an `expiresAt` value calculated at creation. The service deletes expired sessions during startup and then hourly.

The default is 48 hours:

```json
"sessionLifetimeHours": 48
```

Opening or reconnecting to a session does not extend its expiry.

## Configuration security

The Docker Compose mount is read-only:

```text
./config:/app/config:ro
```

The application has no route for changing `config.json`, `teams.json`, or `maps.json`.

The repository also ignores all live files under `config/`. Only `config.example/` is intended to be committed. This prevents a normal `git add .` from accidentally publishing the production `secretKey` or private deployment-specific configuration.

Changing JSON configuration does not modify application code and does not require rebuilding the Docker image. Restarting is not required for team/map changes used by new sessions: the service reloads the JSON before exposing session options and immediately before session creation.

Do not rotate `secretKey` while existing sessions still need to be used: session URLs are authenticated with HMAC tokens derived from that key.

## Reverse proxy

Point your reverse proxy at port `5300` and enable WebSocket support. Both the normal app WebSocket endpoint and Socket.IO need WebSocket-capable proxying.

A single hostname is enough:

```text
mapban.example.com -> container:5300
```

If you want to retain a separate `mapban-socket.example.com` hostname for Spectra configuration, both hostnames can proxy to this same container.

## Spectra frontend integration

Set the Spectra frontend runtime `mapbanEndpoint` to this service's external URL, for example:

```text
https://mapban.example.com
```

The service implements the existing frontend flow:

```text
Socket.IO connect
  -> emit "logon" with { sessionId }
  -> receive "session_data"
```

Every veto state change emits a full new `session_data` object.

The compatibility payload intentionally reports `isSupporter: false`; it does not bypass Spectra's upstream supporter/watermark behavior.

## Producer workflow

1. Open the map-ban site.
2. **Teams screen:** choose Team A and Team B from the configured dropdown or use manual entry.
3. Press **Create Session & Continue**. The 48-hour session and all links are created immediately.
4. **Format & Map Pool:** choose BO1/BO3/BO5, optional BO5 double-ban advantage, Team A/B order, and maps. Every change autosaves. You can close this page and reopen the admin link later without losing the draft.
5. Share the team links and prepare the Spectra overlay at any time; team actions remain blocked while the session is configuring.
6. Select exactly seven maps and press **Start Veto**. This locks setup and enables the first team action.
7. During/after the veto the producer can act for the current team, undo the last veto action, and set per-map Team A/Team B scores. Saved scores are sent in the Spectra `selectedMaps[].score` payload.
8. After the veto is complete, **Publish to Discord** posts the map outcome without scores if `DISCORD_WEBHOOK_URL` is configured.
9. **Reset Veto** is available even before the first ban. Reset clears veto actions and scores, returns to Format & Map Pool, unlocks setup, and keeps the same teams, session ID, links, and original expiry.

## Development checks

```bash
python -m unittest discover -s tests -v
python -m compileall app tests
```

To validate configuration directly:

```bash
CONFIG_DIR=./config.example python - <<'PY'
from app.config import ConfigManager
ConfigManager()
print("Configuration valid")
PY
```

## Map artwork for the Spectra-style UI

The web UI looks for optional local map artwork at `app/static/maps/<map-id>.webp` (for example `app/static/maps/ascent.webp`). If a file is missing, the map card falls back to a built-in gradient and map name, so artwork is not required for the service to function.

This makes it possible to drop in the exact browser-downloaded map assets later without changing JavaScript or JSON configuration.
