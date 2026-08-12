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

**Important:** On deploy, the container waits up to `SHUTDOWN_GRACE_SEC` (default **180s** / 3 min) for in-flight jobs to finish before exit. Set `stop_grace_period` in compose slightly above that (**190s**). Use `/cancel` in Discord to stop a run early.

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
| Admin UI | `src/zen_agent_bot/web/app.py` (`/status` live jobs) |
| Cursor CLI / OpenRouter | `src/zen_agent_bot/backends/` |
| Prompt build | `src/zen_agent_bot/skills/loader.py` |

## Git commits (mandatory)

**Maxi is the only author — always.** No exceptions.

- **Never** add `Co-authored-by:` (or any other co-author trailer) to commit messages — not for Cursor, not for AI, not for any tool or hook reason.
- Commits must show **only** `maximillian1508 <maximillian1508@gmail.com>` as author and committer.
- **Do not commit or push** unless the user explicitly asks.
- Never commit: `.env`, `data/gateway.db`, `data/config.yaml`, tokens, or real Discord/Telegram IDs.

### Conventional Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) for every message:

```text
<type>[optional scope]: <short description>

[optional body]

[optional footer]
```

**Types** (use the most accurate one):

| Type | When |
|------|------|
| `feat` | New feature or user-facing capability |
| `fix` | Bug fix |
| `docs` | README, skills, comments only |
| `refactor` | Code change that neither fixes nor adds feature |
| `test` | Tests only |
| `chore` | Deps, CI, tooling, gitignore |
| `perf` | Performance improvement |
| `build` | Docker, compose, packaging |

**Examples:**

```text
feat(gateway): add /cancel for in-flight agent jobs
fix(discord): show queued status when thread is busy
docs: update deploy steps in README
chore(deps): bump fastapi to 0.141.1
```

Rules:

- Subject line: imperative mood, lowercase after the colon, no period, ~72 chars max
- Scope is optional but preferred when touching one area (`gateway`, `discord`, `admin`, `docker`, `store`)
- One logical change per commit when possible
- Body only when the *why* isn’t obvious from the subject

If `git commit` injects a co-author trailer (IDE/hook behavior), rewrite the commit without it:

```bash
# write message to a file (no co-author lines), then:
git commit-tree HEAD^{tree} -p HEAD^ -F /path/to/message.txt
git reset --hard <new-sha>
```

Or amend with `-F message.txt` and verify with `git log -1` before push.

## Conventions

- Python 3.12 + uv; asyncio single process
- Minimize diff scope; match existing style
- Admin edits to allowlist apply live; **new bots / token changes need container restart**

## Backlog (prioritized — pick from ROADMAP/FEATURES)

See also **Decisions (2026-08-12)** in `ROADMAP.md`.

**P1 next**

1. ~~**`/cancel`**~~ ✅
2. ~~**Graceful shutdown**~~ ✅
3. ~~**Admin live status**~~ ✅ — `/status`, `/api/status`, running jobs, last errors, `agent status`
4. **Telegram enable** — transport coded; flip in admin + tokens when user wants
5. **Claude Code backend** — `claude -p` subprocess adapter

**Done (out of prior P2):** OpenRouter chat backend (`OPENROUTER_API_KEY`; set agent `default_backend` to `openrouter`; chat-only).

**P2 / Phase 2–3 (wanted)**

- Session hygiene — prune stale SQLite mappings, `/close`, admin stale-sessions
- Master slash dispatch — `/run <agent> …` from manager (keep 1-bot-per-profile primary)
- OpenClaw-style bindings / channel→agent routing
- Per-thread `/backend` override
- cursor-sdk local (stream + cancel)
- Cron / scheduled jobs
- Job-done Discord notification
- Optional @mention wake in shared channels (default stays dedicated `#agent` channels)

**Out of scope / defer**

- Interactive tool approve (Accept/Deny) — **P3+ after cursor-sdk**; today `--force` auto-approves; near-term = `/cancel` + allowlist
- Persistent memory (Hermes-style)
- 20+ chat platforms (use OpenClaw)
- Voice

## Self-deploy (manager rebuilds zenbot)

The bot container has **no Docker socket**. Rebuilds go through a **host systemd path watcher**:

1. Manager writes `data/REQUEST_REBUILD` (slash `/rebuild` or agent code)
2. Host unit `zenbot-rebuild.path` fires → `scripts/deploy.sh` (15s delay → build → recreate)
3. User pings the **same thread** after `/health` OK

**One-time host install** (SSH to zenbook, not inside the container):

```bash
sudo /home/maxi/apps/zen-agent-bot/scripts/install-rebuild-watcher.sh
```

**Agent / Discord:** `/rebuild` on the **manager** bot (allowlisted). Or from shell inside a job:

```python
# gateway.request_rebuild(reason="…")  # writes data/REQUEST_REBUILD
```

Logs: `data/logs/rebuild.log` and `journalctl -u zenbot-rebuild.service -f`

Do **not** call `docker compose` from inside the container.

## Testing checklist

- `uv run zen-agent-bot` starts; Discord bots connect
- Post in `#agent` → thread + streaming status + final reply
- Follow-up while busy → queued message, then runs
- Admin: allowlist add/remove without restart
- After code change: `docker compose build && up -d`; verify `/health`

## Delegation

Music library imports → user should use **@ZenMusic** (`music-playlist-download` skill). Manager owns **this gateway** and general server work.
