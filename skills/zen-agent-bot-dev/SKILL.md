---
name: zen-agent-bot-dev
description: >-
  Develop, deploy, and extend zen-agent-bot (Discord/Telegram gateway → Cursor
  Agent CLI). Use when building features, fixing bugs, updating admin UI, Docker
  deploy, SQLite config, transports, backends, or when the user asks to
  continue work on zenbot / zen-agent-bot.
---

# zen-agent-bot development

## Repo & paths

| Item | Path |
|------|------|
| Source (git) | `~/apps/zen-agent-bot` |
| GitHub | `git@github.com:maximillian1508/zenbot.git` |
| Deploy stack | `/srv/apps/zen-agent-bot/docker-compose.yml` (build context = source tree) |
| Admin UI | `https://agents.maximillianleonard.dev` (Traefik → container `:8787`) |
| Runtime data | `~/apps/zen-agent-bot/data/gateway.db` (SQLite; gitignored) |
| Secrets | `~/apps/zen-agent-bot/.env` only — **never commit** |

Read before coding: `ROADMAP.md`, `ARCHITECTURE.md`, `FEATURES.md`, `README.md`.

## Architecture (short)

```text
Discord/Telegram → transports → Gateway (queue + sessions) → backends (cursor-cli)
                                      ↓
                              SQLite (agents, allowlist, sessions, settings)
                              FastAPI admin UI (:8787)
```

- **One Discord bot token per agent profile** (manager, music, …)
- **SOUL** = `agents/<id>/SOUL.md`; **skills** = extra markdown injected every prompt (paths in SQLite `agents.skills`)
- **Sessions** = per thread key → Cursor `--resume` session_id
- **Per-thread queue** = follow-ups while busy auto-run next (same thread)
- **Streaming** = `agent --output-format stream-json` → edits Discord status live (`STREAMING=false` to disable)

## Config split

| SQLite (`gateway.db`) | `.env` only |
|------------------------|-------------|
| Agent profiles, channels, token_env **names** | `DISCORD_TOKEN_*`, `TELEGRAM_TOKEN_*` |
| Allowlist user IDs | `ADMIN_PASSWORD`, `DISCORD_GUILD_ID` |
| Sessions, settings | Cursor mount paths, `AGENT_*` overrides |

First-run seed: `data/config.yaml` → migrates to SQLite once if DB empty.

## Develop locally

```bash
cd ~/apps/zen-agent-bot
set -a && source .env && set +a
uv run zen-agent-bot
```

## Deploy (Docker)

```bash
cd /srv/apps/zen-agent-bot
docker compose build && docker compose up -d --force-recreate
docker compose logs -f --tail=50 zen-agent-bot
curl -sS http://127.0.0.1:8787/health
```

**Important:** Wait for in-flight agent jobs to finish before recreate — mid-deploy kill orphans Discord status messages and loses the run. No graceful shutdown yet (backlog item).

Container needs host mounts: `~/.local/share/cursor-agent`, `agent` binary, `~/.cursor`, `~/.config/cursor`, `~/apps/zen-agent-bot/data`, `~/apps/zen-agent-bot/agents`.

## Code map

| Area | Path |
|------|------|
| Entry | `src/zen_agent_bot/main.py` |
| Config load | `src/zen_agent_bot/config/load.py` |
| SQLite store | `src/zen_agent_bot/store.py` |
| Gateway + queue | `src/zen_agent_bot/gateway/router.py` |
| Cursor CLI backend | `src/zen_agent_bot/backends/cursor_cli.py` |
| Discord / Telegram | `src/zen_agent_bot/transports/` |
| Admin UI | `src/zen_agent_bot/web/app.py` |
| Prompt build | `src/zen_agent_bot/skills/loader.py` |

## Conventions

- Python 3.12 + uv; asyncio single process
- Minimize diff scope; match existing style
- Don't commit `.env`, `data/gateway.db`, `data/config.yaml`
- Admin edits to allowlist apply live; **new bots / token changes need container restart**
- Sole author commits — no `Co-authored-by` trailers

## Backlog (prioritized — pick from ROADMAP/FEATURES)

**P1 next**

1. **`/cancel`** — kill running `agent` subprocess; track PIDs per session
2. **Graceful shutdown** — `stop_grace_period` + SIGTERM handler; finish or cancel jobs cleanly on deploy
3. **Admin live status** — running jobs, last error, `agent status`
4. **Telegram enable** — transport coded; flip in admin + tokens when user wants
5. **Claude Code backend** — `claude -p` subprocess adapter

**P2 later**

- cursor-sdk local (stream + cancel)
- Cron / scheduled jobs
- Job-done Discord notification
- OpenRouter chat backend
- Per-thread `/backend` override

**Out of scope / defer**

- Interactive tool approve (needs SDK bridge)
- 20+ chat platforms (use OpenClaw)
- Voice

## Testing checklist

- `uv run zen-agent-bot` starts; Discord bots connect
- Post in `#agent` → thread + streaming status + final reply
- Follow-up while busy → queued message, then runs
- Admin: allowlist add/remove without restart
- After code change: `docker compose build && up -d`; verify `/health`

## Delegation

Music library imports → user should use **@ZenMusic** (`music-playlist-download` skill). Manager owns **this gateway** and general server work.
