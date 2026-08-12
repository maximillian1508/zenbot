# zen-agent-bot

Headless **agent gateway**: Discord (and optional Telegram) bots → Cursor Agent CLI, with **multiple agent profiles** (manager, music, …).

| Item | Value |
|------|--------|
| Admin UI | `https://agents.example.com` (via Traefik) or `http://127.0.0.1:8787` |
| Stack | Docker Compose in project root |
| Image | Local build (`Dockerfile`: Python 3.12 + uv) |
| Config | SQLite `data/gateway.db` (agents, allowlist, sessions) |
| Secrets | `.env` (mode `600`) — template `.env.example` |
| Auth | Allowlist of Discord/Telegram user IDs; optional `ADMIN_PASSWORD` on admin UI |

## Start / update

```bash
# from project root
cp .env.example .env && chmod 600 .env
cp data/config.example.yaml data/config.yaml   # optional first-run seed
# edit .env (tokens) and data/config.yaml (channel IDs, allowlist)
docker compose up -d --build
docker compose logs -f --tail=50
curl -sS http://127.0.0.1:8787/health
```

Requires **Cursor CLI** logged in on the host (`agent login` / `agent status`). Docker mounts your Cursor agent binary and config — see `.env.example` mount paths.

## Configure

1. Create **one Discord bot per profile** ([Developer Portal](https://discord.com/developers/applications)) — enable **Message Content Intent**.
2. Copy `.env.example` → `.env`; set `DISCORD_TOKEN_*`, optional `DISCORD_GUILD_ID`, `ADMIN_PASSWORD`.
3. Copy `data/config.example.yaml` → `data/config.yaml`; set `allowed_user_ids`, `agent_channel_id` per profile.
4. First start migrates YAML → SQLite if `gateway.db` is empty. After that, use the **admin UI** or SQLite as source of truth.

Run without Docker:

```bash
set -a && source .env && set +a
uv run zen-agent-bot
```

## Usage

| Action | How |
|--------|-----|
| **New task** | Post in the agent channel → bot creates a **thread** and runs Cursor agent |
| **Follow-up** | Reply in the same thread (`--resume`). If a job is running, your message is **queued** |
| **Fresh session** | `/new` in the thread |
| **Cancel in-flight job** | `/cancel` in the thread |
| **Rebuild container** | `/rebuild` on **manager** only (needs host watcher — see below) |
| **Check session** | `/status` (Discord) or admin **Status** page |
| **Live jobs / errors** | Admin UI → **Status** (auto-refresh) |
| **List fleet** | `/agents` on the **manager** bot |

Status messages **stream live** during runs (`STREAMING=false` to disable).

## OpenRouter (optional)

Chat-only backend (no shell/tools). Set `OPENROUTER_API_KEY` in `.env`, optionally `OPENROUTER_MODEL`, then set an agent's **default backend** to `openrouter` in the admin UI and **restart**. Good for cheap Q&A; keep `cursor-cli` for server/code work.

## Telegram (optional)

Transport is implemented but **off by default**. Enable per agent in admin UI after adding `TELEGRAM_TOKEN_*` to `.env` and your Telegram user ID to the allowlist. Restart the container after enabling.

## Layout

```text
data/config.example.yaml  # seed template (committed)
data/gateway.db           # runtime config (gitignored)
agents/*/SOUL.md          # system prompts per profile
src/zen_agent_bot/        # gateway, transports, admin UI
```

See [ARCHITECTURE.md](./ARCHITECTURE.md), [FEATURES.md](./FEATURES.md), [ROADMAP.md](./ROADMAP.md).

## Stack

- **Python 3.12 + uv**, asyncio gateway
- **discord.py** + **python-telegram-bot**
- **`agent -p --force --resume`** — Cursor CLI (subscription quota, not metered API)

`AGENT_FORCE=true` auto-approves tool runs (required for unattended use). Keep the allowlist restricted to yourself.

## Deploy notes

Self-rebuild (no Docker inside the bot container):

```bash
# one-time on the host
sudo /home/maxi/apps/zen-agent-bot/scripts/install-rebuild-watcher.sh

# then either:
#   /rebuild  on @ZenManager (after this image is live), or
#   echo reason | tee ~/apps/zen-agent-bot/data/REQUEST_REBUILD
```

Host waits ~15s, then `docker compose build && up -d --force-recreate`. Grace period on stop is ~3 min (`stop_grace_period` / `SHUTDOWN_GRACE_SEC`). Optional mounts (music library, `/srv/apps`) live in the host compose file.

## systemd (optional, bare metal)

```ini
[Unit]
Description=zen-agent-bot gateway
After=network-online.target

[Service]
Type=simple
User=you
WorkingDirectory=/path/to/zen-agent-bot
EnvironmentFile=/path/to/zen-agent-bot/.env
ExecStart=/path/to/uv run zen-agent-bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```
