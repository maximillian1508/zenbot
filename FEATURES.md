# zen-agent-bot — feature comparison & backlog

Compares **zen-agent-bot** (ours) vs reference stacks. Status updated **2026-08-12** after multi-bot Discord + profiles.

**Legend:** ✅ done · ⚠️ partial · ❌ not yet · **★ might be useful** = prioritized for zenbook

---

## Comparison matrix

| Feature | zen-agent-bot | OpenClaw | Hermes | CursorRemote |
|---------|---------------|----------|--------|--------------|
| Discord | ✅ | ✅ (+ Slack, TG, WA, …) | ✅ (+ 20+ platforms) | ✅ (optional) |
| Telegram | ⚠️ code exists, not enabled | ✅ | ✅ | ❌ |
| Cursor agent CLI | ✅ native | ✅ via plugin | ⚠️ shell only | ❌ |
| Claude Code CLI | ❌ | ✅ | ⚠️ | ❌ |
| Codex / Pi / OpenCode | ❌ | ✅ | ❌ | ❌ |
| OpenRouter / API models | ❌ | ✅ | ✅ core | ❌ |
| New task / session | ✅ thread + `/new` | ✅ per binding | ✅ per chat | ✅ new chat tab |
| Resume conversation | ✅ `--resume` | ✅ sessions | ✅ session store | ✅ same tab |
| Multi-agent routing | ✅ **one bot per profile** | ✅ bindings | ✅ toolsets | ✅ topics per tab |
| Auto tool approve | ✅ `--force` | ✅ per backend | ✅ policy | ✅ inline buttons |
| Approve from phone | ❌ all-or-nothing | ⚠️ depends | ✅ interactive | ✅ best |
| Streaming progress | ❌ final reply only | ✅ | ✅ | ✅ live mirror |
| Persistent memory | ❌ | ✅ | ✅ strong | ❌ |
| Skills / SOUL / context | ✅ **per profile** | ✅ skills | ✅ | ❌ |
| Cron / scheduled jobs | ❌ | ✅ | ✅ | ❌ |
| Subagents / parallel | ❌ | ✅ | ✅ | ❌ |
| MCP servers | ❌ (agent’s own) | ✅ bridge | ✅ | ❌ |
| Voice | ❌ | ⚠️ nodes | ✅ | ❌ |
| **Web admin UI** | ⚠️ **live (Docker)** | ✅ | ⚠️ | ✅ web client |
| Multi-workspace | ✅ per agent profile | ✅ per agent | ✅ | N/A |
| Auth / allowlist | ✅ `allowed_user_ids` | ✅ pairing | ✅ | ✅ TG register |
| Headless (no Cursor IDE) | ✅ | ✅ | ✅ | ❌ needs IDE |
| Setup effort | ~30 min | 2–4 hrs | 2–4 hrs | laptop + patch |
| Maintenance | Low (yours) | Medium | Medium | Medium (UI breaks) |

---

## Add to zen-agent-bot — effort & priority

| Feature | Today | Effort | Priority | Notes |
|---------|-------|--------|----------|-------|
| **Web admin UI** | ⚠️ building | 🟡 2–3d | **P0 — now** | Config, allowlist, sessions, restart hint |
| **systemd unit** | ❌ | 🟢 30m | **P0 ★** | Auto-start + restart on crash |
| **Allowlist in admin** | ✅ YAML only | 🟢 | **P0 ★** | Edit `allowed_user_ids` in UI |
| Claude Code backend | ❌ | 🟢 | **P1 ★** | `claude -p` subprocess adapter |
| Streaming replies | ❌ | 🟡 | **P1 ★** | Edit Discord message during run |
| Telegram transport | ⚠️ coded | 🟢 | **P1 ★** | Enable second profile tokens |
| Per-thread `/backend` | ❌ | 🟢 | **P1 ★** | Store in `sessions.json` |
| Job queue + `/cancel` | partial sem | 🟡 | **P1 ★** | Cap concurrent `agent` runs |
| Codex / other CLIs | ❌ | 🟢 each | P2 | ~50 lines per adapter |
| cursor-sdk local | ❌ | 🟡 | P2 ★ | Stream + cancel; same subscription |
| Cron schedules | ❌ | 🟡 | P2 ★ | apscheduler + stored prompts |
| Multi-workspace routing | ⚠️ per profile | 🟡 | P2 | Channel → workspace bindings |
| OpenRouter chat | ❌ | 🟠 | P2 | API $; chat-only unless tool loop |
| OpenClaw-style bindings | ⚠️ 1:1 bots | 🟠 | P3 | YAML `routing:` rules |
| Notifications on job done | ❌ | 🟢 | P3 ★ | Discord ping when import finishes |
| Interactive approve/deny | ❌ | 🔴 | defer | Needs SDK bridge; CLI is force-or-nothing |
| Persistent memory | ❌ | 🟠 | defer | Files/skills first; not full Hermes |
| MCP tool bridge | ❌ | 🔴 | defer | OpenClaw territory |
| 20+ chat platforms | ❌ | 🔴 each | out of scope | Use OpenClaw |
| Voice | ❌ | 🔴 | out of scope | STT/TTS gateway |
| CursorRemote-style IDE approve | ❌ | 🔴 | wrong tool | Headless zenbook |

---

## Rough effort totals (solo, zenbook)

| Goal | Path | Time |
|------|------|------|
| Discord + Cursor + multi-agent | zen-agent-bot | **Done** |
| + admin UI + allowlist + systemd | Extend zen-agent-bot | **~2–3 days** ← now |
| + Claude, streaming, Telegram live | Extend zen-agent-bot | 🟡 1–2 days |
| + cron, OpenRouter, cursor-sdk | Extend zen-agent-bot | 🟠 ~1 week |
| Full multi-platform gateway | OpenClaw + plugins | 2–4 hrs setup, don’t fork |
| API brain + deep memory | Hermes | 2–4 hrs setup; not Cursor-native |
| Approve steps from phone on laptop Cursor | CursorRemote | Separate from zenbook |

---

## Current build order

1. ~~Refactor backends / transports / profiles~~ ✅  
2. ~~Multi-bot Discord~~ ✅  
3. ~~Skills + config.yaml~~ ✅  
4. **FastAPI admin UI** ← **active**  
5. systemd + allowlist polish  
6. Telegram enable + streaming + `/cancel`  
7. Extra backends (claude-cli, cursor-sdk, openrouter)

See [ROADMAP.md](./ROADMAP.md) for billing and phase detail.
