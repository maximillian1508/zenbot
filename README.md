# zen-agent-bot

Headless **agent gateway**: Discord (and optional Telegram) bots → Cursor Agent CLI, with **multiple agent profiles** (manager, music, …).

| Item | Value |
|------|--------|
| Admin UI | `https://agents.maximillianleonard.dev` (Traefik) or `http://127.0.0.1:8787` |
| Runtime | **Host systemd + `uv`** on Linux (preferred) · **launchd LaunchAgent + `uv`** on macOS — Docker compose kept as legacy |
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

## Start / update (macOS — launchd)

Same gateway, launchd instead of systemd. Runs as **your user** (a LaunchAgent, not
root) because the agent needs your login context: `~/.cursor` / `~/.claude`
credentials, `~/.ssh` keys, user `PATH`.

```bash
# one-time install (no sudo)
~/apps/zen-agent-bot/scripts/install-launchd-service.sh

# day-to-day
launchctl kickstart -k gui/$(id -u)/dev.maximillianleonard.zen-agent-bot   # restart
tail -f ~/apps/zen-agent-bot/data/logs/launchd.err.log                     # logs
launchctl print gui/$(id -u)/dev.maximillianleonard.zen-agent-bot | head -20

# remove
~/apps/zen-agent-bot/scripts/install-launchd-service.sh --uninstall
```

`/rebuild` works the same way — the flag file is picked up by `WatchPaths`
instead of a systemd path unit.

**Mac-as-server caveats**

- A LaunchAgent runs **only while you are logged in**. For unattended use enable
  automatic login (or convert to a LaunchDaemon, which loses the user credentials
  the agent needs).
- Stop the Mac sleeping or the gateway drops offline: `sudo pmset -a sleep 0`.
- launchd has **no cgroup memory cap**, so the systemd `MemoryMax=6G` guard has no
  equivalent — a runaway agent is bounded only by system pressure.
- The admin UI defaults to `127.0.0.1:8787` here (no Traefik-in-Docker to reach).

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
| **Get files back** | The agent replies with `[[attach: /abs/path]]` and the file is uploaded into the thread (screenshots, charts, exports). Max 10 files, 8 MiB each — `OUTBOUND_MAX_FILES` / `OUTBOUND_MAX_BYTES` to change. Only paths under the workspace, `data/`, or a temp dir; obvious secrets (`.env`, `.ssh/`, `*.pem`) are refused. Discord only for now |
| **Fresh session** | `/new` in the thread (keeps `/model` and `/backend` overrides) |
| **Close session** | `/close` — archive Discord thread, keep `--resume`; admin **Sessions → Clear** forgets the mapping |
| **Model** | `/model` lists Cursor CLI models · `/model composer-2.5` this thread · `/model clear` |
| **Backend** | `/backend` · `/backend cursor-sdk` / `openrouter` this thread · `/backend clear` (clears resume when switching) |
| **Cancel in-flight job** | **Cancel** on the running status bubble · `/cancel` in the thread |
| **Restart gateway** | `/rebuild` on **manager** (host `systemctl restart`) |
| **Check session** | `/status` (Discord) or admin **Status** page |
| **Live jobs / errors** | Admin UI → **Status** (auto-refresh) |
| **List fleet** | `/agents` |
| **Cron** | Admin **Schedules** (create/enable/run) · `/schedule` lists · each schedule keeps **one public Discord thread** and posts every run there |
| **Handoff** | `/handoff agent:manager note:…` from a thread (picker) · **Ask Manager** button on non-manager job-done bubbles |
| **OpenRouter memory** | Last ~20 user/assistant turns in SQLite per thread (replayed each call). `/new` clears it |

Status messages **stream live** during runs (`STREAMING=false` to disable). Long runs and errors append `✅ Done @you · 3m 12s` on the **same** status bubble (not a new message). Send now / `/close` cancellations skip the ping.

## OpenRouter (optional)

Chat-only backend (no shell). Set `OPENROUTER_API_KEY` in admin **Secrets** (live) or `.env`, optionally `OPENROUTER_MODEL` / Settings model. Settings checkbox **OpenRouter web search** (or `OPENROUTER_ONLINE=true`) appends `:online` to the resolved model — live on the next job, extra $. Set an agent's **default backend** to `openrouter` in the admin UI (**restart**) or use `/backend openrouter` in a thread (live). Good for cheap Q&A; keep `cursor-cli` for server/code work.

## Cursor SDK local (optional)

Same Cursor subscription as `cursor-cli`, via `cursor-sdk` (`AsyncClient.launch_bridge`). Stream + `/cancel` + resume by SDK agent id. Set an agent's **default backend** to `cursor-sdk` (restart) or `/backend cursor-sdk` (aliases: `sdk`). Model required — admin **cursor-sdk model**, else cursor-cli / `AGENT_MODEL`, else `composer-2.5`. Needs `CURSOR_API_KEY` (admin Secrets). Session ids do **not** transfer from `cursor-cli`. Accept/Deny is still later.

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

Preferred: **host systemd + uv** (Linux) or **launchd + uv** (macOS). `/rebuild` writes `data/REQUEST_REBUILD` → `zenbot-rebuild.path` (systemd) or `WatchPaths` (launchd) → `scripts/deploy.sh` → `systemctl restart` / `launchctl kickstart -k` (~15s delay, 620s stop timeout so in-flight jobs drain). `deploy.sh` picks the init system from `uname` and honours `ZENBOT_INIT`, `ZENBOT_DRY_RUN`, `ZENBOT_FORCE_RESTART`. Human ops: `~/ZEN-AGENT-BOT.md` (handbook after sync).

```bash
sudo /home/maxi/apps/zen-agent-bot/scripts/install-host-service.sh
```

Legacy Docker compose under `/srv/apps/zen-agent-bot` is stopped by that install script. Traefik routes `agents.maximillianleonard.dev` via file provider → `host.docker.internal:8787`.
