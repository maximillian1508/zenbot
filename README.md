# zen-agent-bot

Headless **agent gateway**: Discord (and optional Telegram) bots → Cursor Agent CLI, with **multiple agent profiles** (manager, music, …).

| Item | Value |
|------|--------|
| Admin UI | `https://agents.maximillianleonard.dev` (Traefik) or `http://127.0.0.1:8787` |
| Runtime | **Host systemd + `uv`** (preferred) — Docker compose kept as legacy |
| Config | SQLite `data/gateway.db` (agents, allowlist, sessions) |
| Secrets | `.env` (mode `600`) — template `.env.example` |
| Auth | Allowlist of Discord/Telegram user IDs; optional `ADMIN_PASSWORD` on admin UI |

## Start / update (host — preferred)

```bash
# one-time migrate off Docker → systemd + uv + Traefik file route
sudo /home/maxi/apps/zen-agent-bot/scripts/install-host-service.sh

# day-to-day
sudo systemctl restart zen-agent-bot
journalctl -u zen-agent-bot -f
curl -sS http://127.0.0.1:8787/health
```

Or Discord **`/rebuild`** on @ZenManager (writes `data/REQUEST_REBUILD` → host restarts the unit).

Requires **Cursor CLI** logged in as `maxi` (`agent login` / `agent status`).

## Configure

1. Create **one Discord bot per profile** ([Developer Portal](https://discord.com/developers/applications)) — enable **Message Content Intent**.
2. Copy `.env.example` → `.env`; set `DISCORD_TOKEN_*`, optional `DISCORD_GUILD_ID`, `ADMIN_PASSWORD`.
3. Copy `data/config.example.yaml` → `data/config.yaml`; set `allowed_user_ids`, `agent_channel_id` per profile.
4. First start migrates YAML → SQLite if `gateway.db` is empty. After that, use the **admin UI** or SQLite as source of truth.

Foreground debug:

```bash
cd ~/apps/zen-agent-bot
set -a && source .env && set +a
uv run zen-agent-bot
```

## Usage

| Action | How |
|--------|-----|
| **New task** | Post in `#agent` / `#music-agent` / `#general-agent`, or `/music` `/general` `/manager` → bot opens a **thread** in that home |
| **Follow-up** | Reply in the same thread (`--resume`). If a job is running, your message is **queued** — **Send now** stops the current job and runs this one (Cursor Stop & send). **Drop** unqueues it |
| **Attach files** | Drop images/docs in the thread (or with your message) — saved under `data/attachments/` and paths go into the prompt |
| **Fresh session** | `/new` in the thread (keeps `/model` and `/backend` overrides) |
| **Close session** | `/close` — drop resume + overrides; also admin **Sessions** |
| **Model** | `/model` lists Cursor CLI models · `/model composer-2.5` this thread · `/model clear` |
| **Backend** | `/backend` · `/backend openrouter` this thread · `/backend clear` (clears resume when switching) |
| **Cancel in-flight job** | `/cancel` in the thread |
| **Restart gateway** | `/rebuild` on **manager** (host `systemctl restart`) |
| **Check session** | `/status` (Discord) or admin **Status** page |
| **Live jobs / errors** | Admin UI → **Status** (auto-refresh) |
| **List fleet** | `/agents` |

Status messages **stream live** during runs (`STREAMING=false` to disable). Long runs and errors append `✅ Done @you · 3m 12s` on the **same** status bubble (not a new message). Send now / `/close` cancellations skip the ping.

## OpenRouter (optional)

Chat-only backend (no shell). Set `OPENROUTER_API_KEY` in admin **Secrets** (live) or `.env`, optionally `OPENROUTER_MODEL` / Settings model. Settings checkbox **OpenRouter web search** (or `OPENROUTER_ONLINE=true`) appends `:online` to the resolved model — live on the next job, extra $. Set an agent's **default backend** to `openrouter` in the admin UI (**restart**) or use `/backend openrouter` in a thread (live). Good for cheap Q&A; keep `cursor-cli` for server/code work.

## Claude Code (optional)

Coding agent via `claude -p` (Claude Pro/Max subscription). Install Claude Code on the host, run `claude` once to log in, set an agent's **default backend** to `claude-cli`, then **restart**. Uses `--dangerously-skip-permissions` when `CLAUDE_FORCE=true` (default). Use `/new` when switching backends on a thread (session IDs are not shared with cursor-cli).

## Telegram (optional)

Transport is implemented but **off by default**. Enable per agent in admin UI after adding `TELEGRAM_TOKEN_*` to `.env` and your Telegram user ID to the allowlist. Restart the service after enabling.

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

Preferred: **host systemd + uv**. `/rebuild` → `zenbot-rebuild.path` → `scripts/deploy.sh` → `systemctl restart zen-agent-bot` (~15s delay, `TimeoutStopSec=190`).

```bash
sudo /home/maxi/apps/zen-agent-bot/scripts/install-host-service.sh
```

Legacy Docker compose under `/srv/apps/zen-agent-bot` is stopped by that install script. Traefik routes `agents.maximillianleonard.dev` via file provider → `host.docker.internal:8787`.
