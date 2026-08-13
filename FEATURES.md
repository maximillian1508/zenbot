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
| OpenRouter / API models | ✅ chat + optional `:online` | ✅ | ✅ core | ❌ |
| New task / session | ✅ thread + `/new` | ✅ per binding | ✅ per chat | ✅ new chat tab |
| Resume conversation | ✅ `--resume` | ✅ sessions | ✅ session store | ✅ same tab |
| Multi-agent routing | ✅ **one bot per profile** | ✅ bindings | ✅ toolsets | ✅ topics per tab |
| Auto tool approve | ✅ `--force` | ✅ per backend | ✅ policy | ✅ inline buttons |
| Approve from phone | ❌ all-or-nothing | ⚠️ depends | ✅ interactive | ✅ best |
| Streaming progress | ✅ Discord edit | ✅ | ✅ | ✅ live mirror |
| Persistent memory | ❌ | ✅ | ✅ strong | ❌ |
| Skills / SOUL / context | ✅ **per profile** | ✅ skills | ✅ | ❌ |
| Cron / scheduled jobs | ❌ | ✅ | ✅ | ❌ |
| Subagents / parallel | ❌ | ✅ | ✅ | ❌ |
| MCP servers | ❌ (agent’s own) | ✅ bridge | ✅ | ❌ |
| Voice | ❌ | ⚠️ nodes | ✅ | ❌ |
| **Web admin UI** | ✅ + live status | ✅ | ⚠️ | ✅ web client |
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
| Claude Code backend | ✅ | — | **done** | `claude -p`; set `default_backend` to `claude-cli` |
| Streaming replies | ✅ | — | **done** | Discord status edit |
| Telegram transport | ⚠️ coded | 🟢 | **P1 ★** | Enable second profile tokens |
| File attachments | ✅ | — | **done** | Discord/TG → `data/attachments/`; paths in prompt (25 MiB / 10) |
| Per-thread `/backend` | ✅ | — | **done** | Same session row as `/model`; resume cleared on switch |
| **Model selection** | ✅ | — | **done** | Admin fields + `/model` in thread; live apply |
| Job queue + `/cancel` | ✅ | — | **done** | Graceful shutdown + `/rebuild` too |
| **Queue Send now button** | ✅ | — | **done** | Discord Stop & send + Drop; see ROADMAP |
| OpenRouter chat | ✅ | — | **done** | Chat-only; optional `:online` search toggle |
| Admin live status | ✅ | — | **done** | `/status` + `/api/status` |
| Codex / other CLIs | ❌ | 🟢 each | P2 | ~50 lines per adapter |
| cursor-sdk local | ❌ | 🟡 | P2 ★ | Stream + cancel; same subscription |
| Cron schedules | ❌ | 🟡 | P2 ★ | apscheduler + stored prompts |
| Multi-workspace routing | ⚠️ per profile | 🟡 | P2 | Channel → workspace bindings |
| OpenClaw-style bindings | ⚠️ 1:1 bots | 🟠 | P3 | YAML `routing:` rules |
| Notifications on job done | ✅ | — | **done** | Same-bubble `@you` + duration; skip Send now / `/close` |
| Session hygiene `/close` | ✅ | — | **done** | Archive Discord thread; keep `--resume`; admin Clear deletes mapping |
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
4. ~~FastAPI admin UI + allowlist~~ ✅  
5. ~~`/cancel` + graceful shutdown + self-rebuild~~ ✅  
6. ~~Admin live status + OpenRouter~~ ✅  
7. Telegram enable → ~~Claude backend~~ → ~~file attachments~~ ✅  
8. ~~**Model selection**~~ ✅ — admin default models + `/model` in thread  
9. ~~**Queue Send now**~~ ✅ — Discord Stop & send + Drop on queued follow-ups  
10. ~~**Per-thread `/backend`**~~ ✅  
11. ~~**Job-done ping + `/close`**~~ ✅  
12. ~~**One-bot Discord + `/music` `/general` `/run`**~~ ✅  
13. Phase 2/3: extra bindings · cursor-sdk / cron  

See [ROADMAP.md](./ROADMAP.md) — **Decisions (2026-08-12)** + billing/phase detail.
