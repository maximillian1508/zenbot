# zen-agent-bot — roadmap

Personal headless gateway on **zenbook**: Discord + Telegram → pluggable agent backends → local workspaces (music, general tasks, etc.).

**Docs:** [ARCHITECTURE.md](./ARCHITECTURE.md) (stack + multi-agent) · **this file** (features & billing)

**Status today:** Multi-bot Discord (manager + music), profiles, skills, `config.yaml`, **admin UI** on `127.0.0.1:8787`.

---

## Billing / quota (important)

| Backend | What you pay | API $ per token? | Good for |
|---------|--------------|------------------|----------|
| **Cursor CLI** (`agent -p`) | **Cursor subscription** (Pro/Ultra/etc.) | No — uses plan fast requests / agent quota | Full agent + shell on zenbook ✅ |
| **Cursor SDK — local runtime** | **Same Cursor subscription** | No separate OpenAI-style API bill; uses dashboard **API key for auth**, agent runs **on zenbook** | Streaming, resume, cancel; same quota as CLI |
| **Cursor SDK — cloud runtime** | **Cursor subscription** (cloud agent minutes / cloud credits on plan) | Not OpenRouter-style; still Cursor billing | Long jobs without tying up zenbook |
| **Claude Code CLI** (`claude -p`) | **Claude Pro/Max** subscription (OAuth login) | No — subscription quota | Coding agent with Anthropic tools |
| **OpenRouter** | **OpenRouter credits** (prepaid) or upstream keys | **Yes — pay per token** | Any model (DeepSeek, Gemini, etc.); chat-only unless you add tools yourself |

**Summary:** Cursor CLI, Cursor SDK (local), and Claude Code CLI = **subscription plans**, not metered API. **OpenRouter = always metered API.**

Recommendation: default backend **`cursor-cli`** for zenbook work (music import, verify/fix). Use **OpenRouter** for cheap chat/planning without burning Cursor quota. Use **Claude Code** only if you have Max and want Anthropic’s coding agent specifically.

---

## Target architecture

```text
                    ┌─────────────────────────────────┐
  Discord ─────────►│                                 │
  Telegram ────────►│  zen-agent-bot (gateway)        │
  Web UI (config)──►│  · transport layer              │
                    │  · session router               │
                    │  · job queue                    │
                    └────────────┬────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
   cursor-cli              cursor-sdk              claude-cli
   (subprocess)            (local async)           (subprocess)
         │                       │                       │
         └───────────────────────┴───────────────────────┘
                                 │
                    openrouter (HTTP chat/completions)
                    · no shell unless we build tool loop
```

---

## Feature backlog (prioritized)

### Phase 1 — Core gateway (MVP+)

| # | Feature | Why | Effort | Deps |
|---|---------|-----|--------|------|
| 1.1 | **Unified transport interface** | One `on_message(chat_key, text, meta)` for all channels | 🟢 0.5d | — |
| 1.2 | **Telegram bot** | You use TG; same threads/sessions as Discord | 🟢 1d | `python-telegram-bot` |
| 1.3 | **Discord polish** | Already exists; align with unified interface | 🟢 0.5d | — |
| 1.4 | **Cross-channel session link** | Same `session_id` whether you reply on Discord or Telegram (optional `link` command) | 🟡 1d | shared `sessions.json` keyed by user-defined `project id` |
| 1.5 | **Backend registry** | Pluggable `AgentBackend` protocol | 🟢 0.5d | — |
| 1.6 | **Per-chat backend + workspace** | `/backend cursor` ✅; `/workspace` still later | 🟡 | session `backend` column |
| 1.7 | **Streaming to chat** | Edit one Discord/TG message while agent runs | 🟡 1–2d | `stream-json` |
| 1.8 | **Model selection** | Admin default models + `/model` in thread | ✅ | sessions + settings (see below) |

### Phase 2 — Backends

| # | Feature | Billing | Effort | Notes |
|---|---------|---------|--------|-------|
| 2.1 | **cursor-cli** (done) | Subscription | ✅ | `agent -p --force --resume` |
| 2.2 | **cursor-sdk local** | Subscription | 🟡 1–2d | `AsyncClient.launch_bridge` + stream; needs `CURSOR_API_KEY` |
| 2.3 | **claude-cli** | Claude Max/Pro | 🟢 1d | `claude -p --dangerously-skip-permissions` or sandbox profile |
| 2.4 | **openrouter** | API $ | ✅ | Chat completions; optional `:online` web search toggle |
| 2.5 | **openrouter + tools** (optional later) | API $ | 🟠 1w | Reimplement tool loop or delegate hard tasks to cursor-cli |

**Default routing suggestion:**

| Task type | Default backend |
|-----------|-----------------|
| Music import, server ops, verify/fix | `cursor-cli` |
| Quick questions, drafting, research | `openrouter` |
| Anthropic-only coding preference | `claude-cli` |

### Phase 3 — Web UI (config management)

| # | Feature | Why | Effort |
|---|---------|-----|--------|
| 3.1 | **Config file** `data/config.yaml` | Single source of truth (replaces most `.env`) | 🟢 0.5d |
| 3.2 | **FastAPI admin app** | Local-only config UI | 🟡 2–3d | **v1 done** — allowlist, agents, sessions |
| 3.3 | **Pages: Channels** | Enable Discord/TG, tokens (masked), allowlists | 🟡 |
| 3.4 | **Pages: Backends** | Default backend, API keys, **model names**, `force` flags | 🟡 | model fields in Settings ✅; full Backends page still later |
| 3.5 | **Pages: Routing** | Channel/thread → workspace + backend rules | 🟡 |
| 3.6 | **Pages: Sessions** | View active session IDs, clear, link channels | 🟢 |
| 3.7 | **Auth** | Password or Tailscale-only bind `127.0.0.1:8787` | 🟢 |
| 3.8 | **Live status** | Running jobs, last error, agent login status | ✅ |

**Not in v1 web UI:** full chat (Discord/TG stay the chat UI). Web = **manage**, not replace messaging.

### Phase 4 — Reliability & ops

| # | Feature | Effort |
|---|---------|--------|
| 4.1 | systemd unit + `Restart=on-failure` | 🟢 |
| 4.2 | Job queue (max N concurrent agents) | 🟡 1d (partial ✅) |
| 4.2b | Queue **Send now** button (Stop & send) | ✅ | Discord + Drop; Telegram later |
| 4.3 | `/cancel` in chat | ✅ |
| 4.3b | Graceful shutdown (`stop_grace_period` + SIGTERM) | ✅ |
| 4.4 | Skills prefix (load `~/.cursor/skills/*/SKILL.md` by name) | 🟡 1d |
| 4.5 | Cron (`/schedule` + admin Schedules) | ✅ | New Discord thread per run |
| 4.6 | Notifications (Discord/TG ping when job finishes) | ✅ |
| 4.7 | Session hygiene (`/close`, prune empty mappings) | ✅ |
| 4.8 | `/handoff` + Ask Manager button | ✅ | Transcript → new public thread; pick agent or one-click manager |
| 4.9 | OpenRouter chat-turn window | ✅ | SQLite last ~20 turns; replayed each OpenRouter call |

### Phase 5 — Later / maybe

| Feature | Effort | Note |
|---------|--------|------|
| Master slash dispatch (`/run music …`) | ✅ | One Discord bot; `/music` `/general` `/manager` + channel homes |
| OpenClaw-style bindings | 🟠 | Channel → agent/workspace |
| @mention wake in shared channels | 🟡 | Optional; dedicated `#agent` channels stay default |
| Interactive tool approve (like CursorRemote) | 🔴 | **P3+ after cursor-sdk**; Discord Accept/Deny; CLI `--force` = no prompts |
| Persistent memory (Hermes-style) | 🔴 | Use files/skills first |
| Slack, WhatsApp | 🟡 each | Copy transport pattern |
| Cursor SDK cloud runtime | 🟡 | Offload to Cursor VM |

---

## Config shape (target)

```yaml
# data/config.yaml (edited via web UI or by hand)
server:
  admin_listen: "127.0.0.1:8787"
  admin_password: "…"  # or env ADMIN_PASSWORD

channels:
  discord:
    enabled: true
    token_env: DISCORD_BOT_TOKEN
    agent_channel_id: 123456789
    allowed_user_ids: [987654321]
  telegram:
    enabled: true
    token_env: TELEGRAM_BOT_TOKEN
    allowed_user_ids: [111222333]

backends:
  default: cursor-cli
  cursor-cli:
    command: agent
    force: true
    model: null  # optional
  cursor-sdk:
    runtime: local
    workspace: /home/maxi
    model: composer-2.5
    api_key_env: CURSOR_API_KEY
  claude-cli:
    command: claude
    force: true
  openrouter:
    api_key_env: OPENROUTER_API_KEY
    model: anthropic/claude-sonnet-4

routing:
  - match: { channel: discord, name: "music" }
    workspace: /home/maxi
    backend: cursor-cli
  - match: { channel: telegram }
    workspace: /home/maxi
    backend: cursor-cli
```

---

## Suggested build order (current)

1. ~~Backend registry + cursor-cli + multi-bot Discord + skills + config + admin UI~~ ✅
2. ~~`/cancel` + graceful shutdown + self-rebuild~~ ✅
3. ~~Admin live status~~ ✅
4. ~~OpenRouter chat backend~~ ✅
5. Telegram enable (when tokens ready)
6. ~~Claude Code backend (`claude -p`)~~ ✅
7. ~~**File attachments**~~ ✅ — Discord/Telegram → `data/attachments/` + paths in prompt
8. ~~**Model selection**~~ ✅ — admin fields + per-thread `/model` (session override; share plumbing with `/backend`)
9. ~~**Queue “Send now” button**~~ ✅ — Discord Stop & send (cancel in-flight, run this follow-up)
10. ~~**Per-thread `/backend`**~~ ✅
11. ~~**Job-done ping + `/close`**~~ ✅
12. ~~**One-bot Discord + slash dispatch**~~ ✅
13. ~~cron / scheduled jobs~~ ✅ — admin Schedules + `/schedule`; new Discord thread per run
14. cursor-sdk / extra bindings

---

## Decisions (2026-08-12)

Locked from planning with Maxi — keep these when picking backlog work.

| Topic | Decision |
|-------|----------|
| **Session / thread hygiene** | Defer to **Phase 2/3**. Today: Discord 7-day auto-archive + `/new` + admin clear is enough. Later: prune stale SQLite rows, `/close`, admin stale-sessions view. |
| **Multi-agent UX** | **One Discord bot**, many profiles. Channel home + `/music` `/general` `/manager` (plain `#agent` = manager). Distinct tokens still allowed. |
| **@mention wake (Claude-tag style)** | **Not needed** for now. Dedicated `#agent` / `#music-agent` channels are the wake boundary. Optional P2/P3 if we want bots in shared channels. |
| **Self-rebuild mid-chat** | Flag file `data/REQUEST_REBUILD` + host systemd path unit → `scripts/deploy.sh`. Manager `/rebuild`. No Docker socket in the bot. |
| **Persistent memory / @mention filtering in-model** | Out of scope. Gateway filters by channel/allowlist; no Hermes memory. |
| **Interactive tool approve (Accept/Deny)** | **P3+, after cursor-sdk.** Today CLI uses `--force` (no per-tool prompts). Near-term control = allowlist + `/cancel` + channel isolation. Discord Accept/Deny buttons need an SDK (or non-force) bridge that emits pending tool calls — not on P1/P2. |

**P1 queue:** ~~`/cancel`~~ → ~~graceful shutdown~~ → ~~admin live status~~ → Telegram → ~~Claude backend~~ → ~~file attachments~~.

**Done extras:** OpenRouter chat backend; Claude Code backend; file attachments (Discord/TG → `data/attachments/`, paths in prompt; 25 MiB / 10 files).

**P2/P3 (explicitly wanted):** ~~model selection (admin + `/model`)~~ ✅ · ~~queue Send now button~~ ✅ · ~~per-thread `/backend`~~ ✅ · ~~job-done ping~~ ✅ · ~~`/close` / session hygiene~~ ✅ · ~~one-bot Discord + `/music` `/general`~~ ✅ · ~~cron / Schedules~~ ✅ · ~~`/handoff` + Ask Manager~~ ✅ · bindings/routing · cursor-sdk · OpenRouter.

### Model selection (2026-08-13)

**Shipped.** Admin defaults + `/model` in the thread. Not a process-wide-only knob.

**Admin**

- Settings (or agent form): default model per backend — `backend.cursor-cli.model`, `backend.claude-cli.model`, `backend.openrouter.model`.
- OpenRouter web search: `backend.openrouter.online` (admin checkbox) or env `OPENROUTER_ONLINE` — appends `:online` at resolve time (idempotent).
- Blank = omit `--model` / use that CLI’s default (OpenRouter keeps its current default string).
- Env still wins if set: `AGENT_MODEL`, `CLAUDE_MODEL`, `OPENROUTER_MODEL`.
- Apply **live** (like allowlist): read model at job start, do not bake only at process boot. No restart for model edits.

**Thread**

- `/model` — show effective model (thread → admin/env → CLI default).
- `/model <id>` — set override for **this thread only** (e.g. `composer-2.5`, `sonnet`, `anthropic/claude-sonnet-4`).
- `/model clear` (or `default`) — drop override; next job uses admin/env.
- Store on `sessions.model` (same row as `/backend`).
- In-flight job unchanged; next queued/new job in the thread picks it up.
- Discord slash + Telegram text command.

**Resolve at job start**

1. Thread `/model` override  
2. Admin/env default for the **active backend**  
3. CLI / OpenRouter built-in default  

### Per-thread `/backend` (2026-08-13)

**Shipped.** Same session row as `/model`.

- `/backend` — show effective backend (thread → agent profile default) + model.
- `/backend cursor-cli` / `claude-cli` / `openrouter` (aliases: `cursor`, `claude`, `or`).
- `/backend clear` — drop override; next job uses the agent profile default.
- Changing the **effective** backend clears `--resume` (session ids don’t transfer). `/model` override is kept.
- In-flight job unchanged; next queued/new job picks it up.

### Queue “Send now” (2026-08-13)

**Shipped (Discord).** Button on a **queued** follow-up = Cursor CLI **Stop & send** (2nd Enter / Cmd+Enter), not IDE “steer into the running turn”.

Today: follow-ups while busy sit in the per-thread queue until the current job finishes. `/cancel` stops the runner, then the next queued job starts.

**v1 (Discord)**

- Queued status message gets a **Send now** button (allowlisted users only).
- Click → cancel the in-flight job (keep partial text, same as `/cancel`) → jump **this** follow-up to the front → it runs next.
- Status becomes `⏭ Sending now — stopping current job…`; button disabled after click.
- Several queued messages: each has its own button; Send now on one only promotes that job.
- Optional sibling: **Drop** (unqueue, no cancel).

**Not in v1**

- Inject / steer into a live `agent -p` turn (needs cursor-sdk or a mid-run stdin protocol we don’t have).
- Telegram inline button (same gateway action later).
- Change default from queue-until-done to always-interrupt.

**P3+ (deferred):** interactive tool approve from Discord — depends on cursor-sdk (or equivalent) approval events.

---

## Open questions (decide before coding)

- [ ] **Cross-channel sessions:** one linked session per “project” or keep Discord/TG separate?
- [ ] **OpenRouter role:** chat-only assistant, or never for coding tasks?
- [x] **Web UI exposure:** public with auth (`agents.maximillianleonard.dev`) — current
- [x] **Secrets:** `.env` only; web UI stores `*_env` names, not token values

---

## Out of scope (use OpenClaw/Hermes instead)

- 10+ messaging platforms
- Deep long-term memory / user modeling
- MCP tool bridge at OpenClaw scale
- Voice
- Interactive tool approve from Discord/phone (**P3+ after cursor-sdk**; CLI `--force` has nothing to approve)
