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
| Deploy stack | Host systemd `zen-agent-bot.service` (`uv run`); Traefik file route |
| Admin UI | `https://agents.maximillianleonard.dev` (Traefik → `host.docker.internal:8787`) |
| Runtime data | `~/apps/zen-agent-bot/data/gateway.db` (SQLite; gitignored) |
| Secrets | `~/apps/zen-agent-bot/.env` only — **never commit** |

Read before coding: `ROADMAP.md`, `ARCHITECTURE.md`, `FEATURES.md`, `README.md`.

**Human ops / new host** (not this skill): `~/ZEN-AGENT-BOT.md` · https://docs.maximillianleonard.dev

## Architecture (short)

```text
Discord/Telegram → transports → Gateway (queue + sessions) → backends (cursor-cli)
                                      ↓
                              SQLite (agents, allowlist, sessions, settings)
                              FastAPI admin UI (:8787)
```

- **One Discord bot** (shared token) + channel/`/music`/`/general` → profile. Distinct tokens still start extra clients.
- **SOUL** = `agents/<id>/SOUL.md`; **skills** = extra markdown injected every prompt (paths in SQLite `agents.skills`)
- **Sessions** = per thread key → Cursor `--resume` session_id
- **Per-thread queue** = follow-ups while busy auto-run next (same thread)
- **Streaming** = `agent --output-format stream-json` → edits Discord status live (`STREAMING=false` to disable)

## Config split

| SQLite (`gateway.db`) | `.env` / systemd (fallback) |
|------------------------|-------------|
| Agent profiles, channels, token_env **names** | Process paths: `AGENT_BIN`, `AGENT_WORKSPACE`, `ADMIN_LISTEN` |
| Allowlist, sessions, settings, **secret values** | Env still works; admin Secrets **wins** if set |
| Secret names: `OPENROUTER_API_KEY`, `CURSOR_API_KEY`, `DISCORD_TOKEN_*`, `TELEGRAM_TOKEN_*`, `ADMIN_PASSWORD` | `DISCORD_GUILD_ID` / `AGENT_MODEL` / `OPENROUTER_ONLINE` env still override settings |

First-run seed: `data/config.yaml` → migrates to SQLite once if DB empty.

## Develop locally

```bash
cd ~/apps/zen-agent-bot
set -a && source .env && set +a
uv run zen-agent-bot
```

## Deploy (host systemd + uv — preferred)

```bash
# one-time cutover from Docker
sudo /home/maxi/apps/zen-agent-bot/scripts/install-host-service.sh

sudo systemctl restart zen-agent-bot
journalctl -u zen-agent-bot -f
curl -sS http://127.0.0.1:8787/health
```

**Important:** On restart the gateway waits for in-flight jobs (`SHUTDOWN_GRACE_SEC`). Host unit sets **600s / 10 min** with `TimeoutStopSec=620` — re-run `sudo …/scripts/install-host-service.sh` (or copy the unit + `daemon-reload`) after pulling unit changes. If a job still runs after grace, it is cancelled but **partial text is kept** on the status message. Use `/cancel` to stop early. Self-`/rebuild` while *this* manager job is still running is the footgun: the restart waits, then cancels *you* if you overrun grace.

Unit `MemoryMax` must stay high enough for Cursor agent + node (default **6G**, `MemoryHigh=3G`). A **2G** cap OOM-kills the whole gateway mid-job → Discord stuck on “Agent running…” with no final reply. After unit changes: `sudo install -m 0644 deploy/systemd/zen-agent-bot.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart zen-agent-bot` (or re-run `install-host-service.sh`).

The process runs as **maxi** with normal host access (`~/.ssh`, `agent`, `/srv`, docker CLI if in `docker` group). Admin listens on `0.0.0.0:8787` (required so Traefik-in-Docker can reach the host); Traefik file route exposes `agents.maximillianleonard.dev` on Tailscale. If Discord resumes fail with `attempt to write a readonly database`, chown root-owned files under `~/.cursor` left by the old Docker container.

## Code map

| Area | Path |
|------|------|
| Entry | `src/zen_agent_bot/main.py` |
| Config load | `src/zen_agent_bot/config/load.py` |
| SQLite store | `src/zen_agent_bot/store.py` |
| Gateway + queue | `src/zen_agent_bot/gateway/router.py` |
| Cursor CLI backend | `src/zen_agent_bot/backends/cursor_cli.py` |
| Cursor SDK backend | `src/zen_agent_bot/backends/cursor_sdk.py` |
| Discord / Telegram | `src/zen_agent_bot/transports/` |
| Admin UI | `src/zen_agent_bot/web/app.py` (`/status` live jobs) |
| Cursor / Claude / OpenRouter | `src/zen_agent_bot/backends/` |
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
- Admin edits to allowlist apply live; **new bots / token changes need a service restart** (`systemctl restart` or `/rebuild`)

## Backlog (prioritized — pick from ROADMAP/FEATURES)

See also **Decisions (2026-08-12)** and **Model selection (2026-08-13)** in `ROADMAP.md`.

**P1 next**

1. ~~**`/cancel`**~~ ✅
2. ~~**Graceful shutdown**~~ ✅
3. ~~**Admin live status**~~ ✅ — `/status`, `/api/status`, running jobs, last errors, `agent status`
4. **Telegram enable** — transport coded; flip in admin + tokens when user wants
5. ~~**Claude Code backend**~~ ✅ — `claude -p`; set agent `default_backend` to `claude-cli` (needs host `claude` login)
6. ~~**File attachments**~~ ✅ — Discord/Telegram files → `data/attachments/`; paths injected into prompt (images via Read; other files by path; 25 MiB / 10 files)

**Done (out of prior P2):** OpenRouter chat backend; **model selection**; **queue Send now**; **per-thread `/backend`**; **job-done ping**; **`/close`** + admin prune empty; **cursor-sdk local**.

**P2 / Phase 2–3 (wanted)**

- ~~Master slash dispatch~~ ✅ — one Discord bot; `/music` `/general` `/manager` `/run`
- ~~OpenClaw-style extra bindings (non-home channels)~~ ✅ — admin **Routing** + `route_bindings`
- ~~cursor-sdk local~~ ✅ — `/backend cursor-sdk` (`sdk`); stream + cancel + resume; Accept/Deny still P3+
- ~~Cron / scheduled jobs~~ ✅ — admin **Schedules** + `/schedule`; one Discord thread per schedule
- ~~**`/handoff` + Ask Manager**~~ ✅ — pick agent or one-click manager; new public thread + transcript
- ~~**OpenRouter chat window**~~ ✅ — SQLite last ~20 turns per thread; `/new` clears
- Optional @mention wake in shared channels (default stays dedicated home channels)
- ~~**Backend-aware UX (2026-08-21)**~~ ✅ — `/model` autocomplete/catalog follow the thread's effective backend (Claude ids on `claude-cli`, live OpenRouter catalog on `openrouter`, `agent models` only on Cursor backends)

**P3 — interactive control plane (2026-08-20)**

- ~~SDK approval bridge → Discord **Accept / Deny** on pending tools~~ ✅ — hooks + `/trust approve` + cursor-sdk (opt-in; default is force everywhere)
- ~~Secure prompt via Discord **modal** for sudo~~ ✅ — `SUDO_ASKPASS` + PATH shim (`scripts/sudo-shim/`, `scripts/sudo-askpass.py`) → `/internal/sudo` → Enter-password modal; all backends; password never logged/stored
- ~~Per-thread **`/trust`**~~ ✅ — `force` | `approve` (admin default still later)
- ~~Passwordless **sudoers** allowlist~~ ✅ — `deploy/sudoers/zenbot-ops` installed to `/etc/sudoers.d/`; zenbot systemctl verbs + daemon-reload passwordless, all other sudo → modal; never allowlist maxi-writable scripts
- Telegram inline approve (after Discord v1)
- **Not building:** full Discord terminal / reverse shell

**Out of scope / defer**

- ~~Interactive tool approve~~ → **P3 epic above** (scoped; not deferred indefinitely)
- Persistent memory (Hermes-style)
- 20+ chat platforms (use OpenClaw)
- Voice

## Self-deploy (manager restarts zenbot)

Gateway runs **on the host** (systemd + uv). `/rebuild` does **not** need Docker:

1. Manager writes `data/REQUEST_REBUILD` (slash `/rebuild` or agent code)
2. Host unit `zenbot-rebuild.path` → `scripts/deploy.sh` (15s delay → `systemctl restart zen-agent-bot`)
3. Gateway pings the requester in the same Discord thread / Telegram chat when `/health` is OK

**One-time host install:**

```bash
sudo /home/maxi/apps/zen-agent-bot/scripts/install-host-service.sh
```

Logs: `journalctl -u zen-agent-bot -f`, `data/logs/rebuild.log`, `journalctl -u zenbot-rebuild.service -f`

## Testing checklist

- `systemctl is-active zen-agent-bot` + Discord bots connect
- Post in `#agent` → thread + streaming status + final reply
- Follow-up while busy → queued message + **Send now** / **Drop**; then runs
- Running status bubble shows **Cancel** (same as `/cancel`; keep partial text)
- Job finishes → same status bubble appends `✅ Done @you · 3m`; `/close` archives Discord thread and keeps `--resume`
- Admin **Schedules** + `/schedule`; a due/run-now job posts in that schedule’s existing Discord thread (creates one on first run)
- `/handoff agent:manager` from a thread, or tap **Ask Manager** on a General/music job-done bubble
- Admin: allowlist add/remove without restart
- After code change: `/rebuild` or `sudo systemctl restart zen-agent-bot`; verify `/health`
- `git push` works from agent jobs (host SSH keys)

## Delegation

Music library imports → user should use **@ZenMusic** (`music-playlist-download` skill). Manager owns **this gateway** and general server work.
