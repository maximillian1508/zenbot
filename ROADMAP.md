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
| 1.6 | **Per-chat backend + workspace** | `/backend cursor`, `/workspace /home/maxi` | 🟢 1d | config store |
| 1.7 | **Streaming to chat** | Edit one Discord/TG message while agent runs | 🟡 1–2d | `stream-json` |

### Phase 2 — Backends

| # | Feature | Billing | Effort | Notes |
|---|---------|---------|--------|-------|
| 2.1 | **cursor-cli** (done) | Subscription | ✅ | `agent -p --force --resume` |
| 2.2 | **cursor-sdk local** | Subscription | 🟡 1–2d | `AsyncClient.launch_bridge` + stream; needs `CURSOR_API_KEY` |
| 2.3 | **claude-cli** | Claude Max/Pro | 🟢 1d | `claude -p --dangerously-skip-permissions` or sandbox profile |
| 2.4 | **openrouter** | API $ | 🟡 2d | Chat completions only; **no native shell** — document clearly |
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
| 3.4 | **Pages: Backends** | Default backend, API keys, model names, `force` flags | 🟡 |
| 3.5 | **Pages: Routing** | Channel/thread → workspace + backend rules | 🟡 |
| 3.6 | **Pages: Sessions** | View active session IDs, clear, link channels | 🟢 |
| 3.7 | **Auth** | Password or Tailscale-only bind `127.0.0.1:8787` | 🟢 |
| 3.8 | **Live status** | Running jobs, last error, agent login status | 🟡 |

**Not in v1 web UI:** full chat (Discord/TG stay the chat UI). Web = **manage**, not replace messaging.

### Phase 4 — Reliability & ops

| # | Feature | Effort |
|---|---------|--------|
| 4.1 | systemd unit + `Restart=on-failure` | 🟢 |
| 4.2 | Job queue (max N concurrent agents) | 🟡 1d |
| 4.3 | `/cancel` in chat | 🟡 1d |
| 4.4 | Skills prefix (load `~/.cursor/skills/*/SKILL.md` by name) | 🟡 1d |
| 4.5 | Cron (`/schedule daily …`) | 🟡 2d |
| 4.6 | Notifications (Discord/TG ping when long job finishes) | 🟢 |

### Phase 5 — Later / maybe

| Feature | Effort | Note |
|---------|--------|------|
| Interactive tool approve (like CursorRemote) | 🔴 | Needs SDK + approval UX; CLI is force-or-nothing |
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

## Suggested build order

1. **Backend registry** + **cursor-cli** refactor (already mostly done)
2. **Telegram** transport
3. **config.yaml** + load from file (keep `.env` for secrets)
4. **claude-cli** + **openrouter** backends
5. **cursor-sdk** backend (streaming)
6. **Web admin UI** (Phase 3)
7. Streaming + job queue + `/cancel`

**Rough total:** ~2–3 weeks part-time for Phases 1–3; Phase 4 as needed.

---

## Open questions (decide before coding)

- [ ] **Cross-channel sessions:** one linked session per “project” or keep Discord/TG separate?
- [ ] **OpenRouter role:** chat-only assistant, or never for coding tasks?
- [ ] **Web UI exposure:** localhost + Tailscale only, or public with auth?
- [ ] **Secrets:** all in `.env`, web UI never stores tokens (only `*_env` keys)?

---

## Out of scope (use OpenClaw/Hermes instead)

- 10+ messaging platforms
- Deep long-term memory / user modeling
- MCP tool bridge at OpenClaw scale
- Voice
