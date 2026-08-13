# zen-agent-bot — stack & multi-agent architecture

Design doc for a headless gateway on **zenbook**: multiple specialist agents, Discord + Telegram, pluggable backends (Cursor CLI/SDK, Claude Code, OpenRouter), web config UI.

See also: [ROADMAP.md](./ROADMAP.md) (feature list & billing).

---

## Stack recommendation

### Choice: **Python 3.12 + uv + asyncio**

| Criterion | Python (chosen) | TypeScript (OpenClaw) | Go/Rust |
|-----------|-----------------|----------------------|---------|
| **Cursor SDK** | ✅ official `cursor-sdk` already in project | ✅ `@cursor/sdk` | ❌ |
| **Cursor / Claude CLI** | ✅ subprocess, natural | ✅ subprocess | ✅ subprocess |
| **Discord + Telegram** | ✅ `discord.py`, `python-telegram-bot` | ✅ discord.js, grammy | libraries exist, more glue |
| **Admin web UI** | ✅ FastAPI + HTMX or small SPA | ✅ Express | heavier build |
| **Extend without recompile** | ✅ | ✅ | ❌ |
| **Copy from OpenClaw/Hermes** | patterns, not code | full codebase | rewrite |
| **Single process, many bots** | ✅ asyncio | ✅ | ✅ |
| **Your skills / scripts on zenbook** | ✅ same ecosystem | fine | friction |

**Verdict:** Stay on **Python**. OpenClaw is the best *reference* for routing and multi-agent config; Hermes for gateway/session patterns. We reimplement the thin layers (~2k lines), not fork 100k+ lines of TS/Python monoliths.

### Core dependencies (pinned via `uv.lock`)

| Layer | Library | Role |
|-------|---------|------|
| Runtime | **uv** + CPython 3.12 | Reproducible venv, `uv run` |
| Async | **asyncio** | One process: Discord + Telegram + web + job queue |
| Chat | **discord.py** 2.x | One `Client` per bot token |
| Chat | **python-telegram-bot** 22.x | One `Application` per bot token |
| Cursor (programmatic) | **cursor-sdk** | Local async agent, stream, resume |
| Cursor / Claude (CLI) | **subprocess** | `agent -p`, `claude -p` |
| OpenRouter | **httpx** | Chat completions API |
| Config UI | **FastAPI** + **uvicorn** | Admin on localhost / Tailscale |
| Config file | **YAML** (`pyyaml`) | Human-editable; web UI edits same file |
| Sessions | **JSON** or **SQLite** | Thread ↔ agent session ↔ backend |

### What we deliberately avoid

- **Electron / Cursor IDE** — headless zenbook only
- **Monorepo TS + Python** — one language for gateway + admin
- **Heavy frameworks** (Django, full React admin) — config UI stays small
- **Forking OpenClaw/Hermes** — borrow ideas, own code

### Project layout (target)

```text
zen-agent-bot/
├── pyproject.toml          # uv
├── ROADMAP.md
├── ARCHITECTURE.md         # this file
├── data/
│   ├── config.yaml         # agents, channels, routing (secrets via env refs)
│   └── sessions.db         # optional SQLite
├── src/zen_agent_bot/
│   ├── main.py             # entry: load config, start gateway
│   ├── config/             # load/validate YAML + env
│   ├── gateway/            # job queue, router
│   ├── transports/
│   │   ├── base.py         # Transport protocol
│   │   ├── discord.py      # one Bot class, N instances from config
│   │   └── telegram.py
│   ├── agents/             # agent *profiles* (not Cursor runtime)
│   │   ├── profile.py      # name, workspace, skills, default backend
│   │   └── registry.py
│   ├── backends/
│   │   ├── base.py         # AgentBackend protocol
│   │   ├── cursor_cli.py
│   │   ├── cursor_sdk.py
│   │   ├── claude_cli.py
│   │   └── openrouter.py
│   ├── skills/             # load SKILL.md paths into system prefix
│   └── web/                # FastAPI admin
└── agents/                 # optional per-agent prompt files
    ├── manager/SOUL.md
    ├── music/SOUL.md
    └── general/SOUL.md
```

Patterns copied from elsewhere:

| Idea | From | Our version |
|------|------|-------------|
| `bindings` channel → agent | OpenClaw | `routing:` in `config.yaml` |
| Transport adapter + session store | Hermes | `transports/` + `sessions` |
| CLI backend spawn | OpenClaw `claude-cli` / cursor plugins | `backends/cursor_cli.py` |
| Agent profile (workspace, model) | OpenClaw `agents.list` | `agents:` in YAML |

---

## Multi-agent model

### Concepts

| Term | Meaning |
|------|---------|
| **Agent profile** | Config object: name, role, workspace, skills, default backend, system prompt |
| **Backend** | Runtime that executes a turn: `cursor-cli`, `cursor-sdk`, `claude-cli`, `openrouter` |
| **Transport** | Discord or Telegram connection |
| **Bot identity** | One Discord/Telegram *bot user* (one token) — can map 1:1 to an agent profile |
| **Session** | Cursor/Claude `session_id` (or chat thread) for multi-turn work |
| **Manager** | Primary agent profile: routing, server ops, delegates to specialists |

```text
You (Discord/Telegram)
        │
        ├─► @ZenManager bot  ──► manager profile  ──► cursor-cli  (/home/maxi)
        │
        └─► @ZenMusic bot    ──► music profile    ──► cursor-cli  (/home/maxi + music skills)
```

### Built-in agent profiles (v1)

| Profile ID | Discord bot | Role | Workspace | Skills / prompt |
|------------|-------------|------|-----------|-----------------|
| **manager** | `@ZenManager` (example) | Server ops, planning, delegate, extend stack | `/home/maxi` | general + `create-skill`, infra docs |
| **music** | `@ZenMusic` | MusicGrabber, Navidrome, playlists, verify/fix | `/home/maxi` | `music-playlist-download`, `MUSIC-*` docs |

Manager can **delegate** by telling you to message the other bot, or (Phase 4+) invoke an internal sub-job on the music profile without switching bots.

### Manager vs specialist

| | **Manager** | **Music (specialist)** |
|--|-------------|------------------------|
| Default backend | `cursor-cli` | `cursor-cli` |
| Knows MusicGrabber API | overview | deep |
| Edits `zen-agent-bot` config | yes | no |
| Spotify playlist import + QA | can delegate | primary |
| Shows in Discord as | separate bot user | separate bot user |

---

## Separate Discord users per agent — yes

**Each agent profile that should look like its own user needs its own Discord Application + Bot Token.**

| Approach | Separate avatar/name in Discord? | How |
|----------|----------------------------------|-----|
| **One token, many profiles** | ❌ Same bot user everywhere | Only different channels/threads |
| **One token per agent profile** | ✅ Yes | Recommended |
| **Webhooks (fake username)** | ⚠️ Looks different but not a real bot member | Hacky, no slash commands per identity |

**Telegram:** same rule — **one `@BotFather` bot per agent identity**.

Implementation:

```yaml
agents:
  manager:
    display_name: "Zen Manager"
    discord:
      token_env: DISCORD_TOKEN_MANAGER
      # optional: dedicated channel or DM
    telegram:
      token_env: TELEGRAM_TOKEN_MANAGER
    workspace: /home/maxi
    default_backend: cursor-cli
    skills:
      - ~/.cursor/skills-cursor/create-skill/SKILL.md
    system_prompt_file: agents/manager/SOUL.md

  music:
    display_name: "Zen Music"
    discord:
      token_env: DISCORD_TOKEN_MUSIC
      agent_channel_id: 1234567890
    telegram:
      token_env: TELEGRAM_TOKEN_MUSIC
    workspace: /home/maxi
    default_backend: cursor-cli
    skills:
      - ~/.cursor/skills/music-playlist-download/SKILL.md
    system_prompt_file: agents/music/SOUL.md
```

Gateway starts **two** `discord.Client` instances (asyncio tasks) in **one process**, shared job queue and session DB.

---

## Routing

### v1 — one bot per agent (simple)

Message to `@ZenMusic` → always **music** profile.  
Message to `@ZenManager` → always **manager** profile.

### v2 — bindings (OpenClaw-style)

```yaml
routing:
  - agent: music
    match: { transport: discord, channel_id: "…" }
  - agent: manager
    match: { transport: discord, channel_id: "…" }
  - agent: manager
    match: { transport: telegram }
    default: true
```

### Cross-agent delegation (later)

Manager backend prompt includes: *“For playlist import/QA, ask user to post in #music or @ZenMusic.”*  
Or internal: manager spawns a job on `music` profile and posts result back (no second bot message needed).

---

## Skills integration

Per agent profile:

```yaml
skills:
  - ~/.cursor/skills/music-playlist-download/SKILL.md
  - ~/MUSIC-PLAYLIST-PLAYBOOK.md   # optional context file
```

At run start, gateway **prepends** to the user message (or system block):

```text
[Agent: music | Backend: cursor-cli | Workspace: /home/maxi]

Follow skill(s):
--- music-playlist-download ---
<contents of SKILL.md>
---

User request:
<discord message>
```

Cursor CLI picks up project skills from workspace too; explicit injection guarantees the right playbook per bot.

---

## Backend per profile (defaults)

| Profile | Primary backend | Fallback |
|---------|-----------------|----------|
| manager | `cursor-cli` | — (server/code) |
| music | `cursor-cli` | — (must have shell) |
| general | `openrouter` | chat Q&A; optional `:online` search; no shell |

Per-thread `/model <id>` and `/backend <id>` are live (`sessions.model` / `sessions.backend`). `/new` keeps both overrides and only drops `--resume`. Switching backend clears resume (ids don’t transfer). Resolve backend at job start: thread override → agent profile default. Then resolve model: thread → env → admin `backend.<kind>.model` → CLI default. See [ROADMAP.md](./ROADMAP.md#model-selection-2026-08-13).

Billing reminder: [ROADMAP.md](./ROADMAP.md#billing--quota-important).

---

## Web UI scope

**Admin only** — not a chat client.

- Agent profiles (prompt, skills, workspace, backend)
- Backend default **models** (Settings; live apply)
- Transport tokens (`*_env` names) + **Secrets** page (values masked; SQLite, admin wins over `.env`)
- Routing rules
- Session list / clear / prune empty (`/close` in chat)
- Job queue / logs
- Test button: “ping backend” (`agent status`, `claude --version`)

Listen `127.0.0.1:8787`; expose via **Tailscale**, not public internet.

---

## Performance notes

- **One asyncio process** — low memory vs multiple systemd units
- **Job queue** — cap concurrent `agent` runs (default 2) so zenbook stays responsive
- **Subprocess backends** — Cursor CLI is the heavy part; gateway is lightweight
- **SQLite sessions** — fine to thousands of threads; JSON ok for v1
- **No hot reload of config** — SIGHUP or admin “Apply” restarts transports (v1)

---

## Implementation phases (aligned with ROADMAP)

1. **Refactor** → `backends/`, `transports/base`, `agents/profile`
2. **Discord** → one bot (shared token) + per-profile home channels / slash aliases
3. **Telegram** → same two profiles, two tokens
4. **Skills loader** → per profile
5. **config.yaml** + validation
6. **FastAPI admin**
7. Extra backends (SDK, Claude, OpenRouter)

---

## Open decisions

| Question | Recommendation |
|----------|----------------|
| Linked sessions Discord ↔ Telegram? | Same **profile** shares session store key `profile_id + project_slug`; user runs `/project music` |
| Manager auto-delegate to music? | v2; v1 use two bots |
| How many Discord bots? | **1** (shared token); profiles differ by channel + `/music` `/general` |
| Secrets | Admin **Secrets** (SQLite) or `.env` / systemd `EnvironmentFile`; YAML only `token_env` names |

---

## Summary

| Question | Answer |
|----------|--------|
| Best stack? | **Python 3.12 + uv + asyncio + FastAPI** |
| Separate agents? | **Yes** — agent profiles with own workspace, skills, backend |
| Different Discord users? | **Yes** — **one bot token per profile** |
| Manager + music? | **Yes** — `@ZenManager` + `@ZenMusic` (names up to you) |
| Copy from? | **OpenClaw** routing/bindings, **Hermes** gateway shape, **our** cursor CLI runner |
