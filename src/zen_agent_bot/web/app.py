from __future__ import annotations

import html
import os
import secrets
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..store import ConfigStore

if TYPE_CHECKING:
    from ..gateway import Gateway

security = HTTPBasic(auto_error=False)

BASE_STYLE = """
body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
nav a { margin-right: 1rem; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }
.msg { padding: 0.75rem; background: #e8f5e9; border-radius: 4px; margin-bottom: 1rem; }
.warn { background: #fff3e0; }
.err { background: #ffebee; }
code { background: #f4f4f4; padding: 0.1rem 0.3rem; }
input[type=text], textarea { width: 100%; max-width: 520px; }
textarea { min-height: 4rem; }
label { display: block; margin: 0.6rem 0; }
button { padding: 0.4rem 0.8rem; cursor: pointer; }
.muted { color: #666; font-size: 0.9rem; }
"""


def _admin_password() -> str | None:
    return os.environ.get("ADMIN_PASSWORD", "").strip() or None


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    password = _admin_password()
    if not password:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username.encode(), b"admin")
    pass_ok = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def create_admin_app(*, db: ConfigStore, gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="zen-agent-bot admin", docs_url=None, redoc_url=None)

    def page(title: str, body: str, msg: str = "", *, refresh: int | None = None) -> HTMLResponse:
        nav = """
        <nav>
          <a href="/">Dashboard</a>
          <a href="/status">Status</a>
          <a href="/allowlist">Allowlist</a>
          <a href="/agents">Agents</a>
          <a href="/sessions">Sessions</a>
        </nav>
        """
        banner = f'<div class="msg">{msg}</div>' if msg else ""
        meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
        html_out = (
            f"<!DOCTYPE html><html><head><meta charset=utf-8>{meta}"
            f"<title>{title}</title><style>{BASE_STYLE}</style></head>"
            f"<body>{nav}{banner}<h1>{title}</h1>{body}</body></html>"
        )
        return HTMLResponse(html_out)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(_: None = Depends(require_auth)) -> HTMLResponse:
        agents = db.list_agent_rows()
        allowed = db.allowlist()
        tg_ready = sum(1 for a in agents if a.get("telegram_token_env"))
        pw = _admin_password()
        auth_note = (
            "Protected (ADMIN_PASSWORD set)"
            if pw
            else "<strong>No password</strong> — set ADMIN_PASSWORD in .env"
        )
        live = ""
        if gateway is not None:
            st = gateway.live_status()
            live = (
                f"<li>Live: <strong>{st['running_count']}</strong> running, "
                f"{st['queued_threads']} queued thread(s) — "
                f'<a href="/status">Status</a></li>'
            )
        body = f"""
        <p>Database: <code>{html.escape(str(db.path))}</code></p>
        <p>Auth: {auth_note}</p>
        <ul>
          <li>Agents: {len(agents)}</li>
          <li>Telegram-ready profiles: {tg_ready} (enable in Agents when you add tokens)</li>
          <li>Allowlist: {len(allowed)} user(s) — live, no restart</li>
          <li>Sessions: {len(db.list_sessions())}</li>
          {live}
        </ul>
        <p class="warn">Allowlist/session edits apply immediately.
        <strong>New Discord/Telegram bots</strong> (new agent + token) need a container restart.</p>
        <pre>cd /srv/apps/zen-agent-bot && docker compose up -d --force-recreate</pre>
        """
        return page("Dashboard", body)

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(_: None = Depends(require_auth)) -> HTMLResponse:
        if gateway is None:
            return page("Status", "<p class='warn'>Gateway not attached to admin app.</p>")

        st = gateway.live_status()
        agent_login = await gateway.cursor_agent_status()

        run_rows = []
        for job in st["running"]:
            run_rows.append(
                "<tr>"
                f"<td><code>{html.escape(job['session_key'])}</code></td>"
                f"<td>{html.escape(str(job['agent_id']))}</td>"
                f"<td>{job['elapsed_sec']}s</td>"
                f"<td>{html.escape(job['prompt_preview'])}</td>"
                f"<td>{job['queue_behind']}</td>"
                f"<td>{job['pid'] or '—'}</td>"
                "</tr>"
            )
        q_rows = []
        for item in st["queued"]:
            q_rows.append(
                f"<tr><td><code>{html.escape(item['session_key'])}</code></td>"
                f"<td>{item['queued']}</td></tr>"
            )
        err_rows = []
        for err in st["last_errors"][:10]:
            err_rows.append(
                "<tr>"
                f"<td>{html.escape(_fmt_ts(err['at']))}</td>"
                f"<td>{html.escape(str(err['agent_id']))}</td>"
                f"<td><code>{html.escape(err['session_key'])}</code></td>"
                f"<td>{html.escape(err['error'])}</td>"
                "</tr>"
            )

        flags = []
        if st["shutting_down"]:
            flags.append("<span class='err msg'>Shutting down</span>")
        if st["rebuild_pending"]:
            flags.append("<span class='warn msg'>Rebuild pending</span>")
        flag_html = " ".join(flags) if flags else "<span class='muted'>idle flags clear</span>"

        body = f"""
        <p class="muted">Auto-refresh 5s · <a href="/api/status">JSON</a></p>
        <p>{flag_html}</p>
        <p>Max concurrent: <code>{st['max_concurrent_jobs']}</code> ·
           Streaming: <code>{st['streaming_enabled']}</code> ·
           Backends: <code>{html.escape(', '.join(st['backends']))}</code></p>

        <h2>Cursor agent login</h2>
        <pre>{html.escape(agent_login)}</pre>

        <h2>Running jobs ({st['running_count']})</h2>
        <table>
          <tr><th>Session</th><th>Agent</th><th>Elapsed</th><th>Prompt</th><th>Queued behind</th><th>PID</th></tr>
          {''.join(run_rows) or '<tr><td colspan=6><em>none</em></td></tr>'}
        </table>

        <h2>Queued threads ({st['queued_threads']})</h2>
        <table>
          <tr><th>Session</th><th>Queued</th></tr>
          {''.join(q_rows) or '<tr><td colspan=2><em>none</em></td></tr>'}
        </table>

        <h2>Last errors</h2>
        <table>
          <tr><th>When</th><th>Agent</th><th>Session</th><th>Error</th></tr>
          {''.join(err_rows) or '<tr><td colspan=4><em>none</em></td></tr>'}
        </table>
        """
        return page("Status", body, refresh=5)

    @app.get("/api/status")
    async def api_status(_: None = Depends(require_auth)) -> JSONResponse:
        if gateway is None:
            return JSONResponse({"error": "gateway not attached"}, status_code=503)
        payload = gateway.live_status()
        payload["cursor_agent_status"] = await gateway.cursor_agent_status()
        payload["ts"] = time.time()
        return JSONResponse(payload)

    @app.get("/allowlist", response_class=HTMLResponse)
    async def allowlist_get(_: None = Depends(require_auth)) -> HTMLResponse:
        ids = db.allowlist()
        rows = "".join(f"<tr><td><code>{uid}</code></td></tr>" for uid in ids)
        body = f"""
        <p>Only these user IDs can message the bots (Discord or Telegram). Changes apply immediately.</p>
        <table><tr><th>User ID</th></tr>{rows or '<tr><td><em>empty</em></td></tr>'}</table>
        <h2>Add user</h2>
        <form method="post" action="/allowlist/add">
          <label>User ID<br><input type="text" name="user_id" required pattern="[0-9]+"></label>
          <button type="submit">Add</button>
        </form>
        <h2>Remove user</h2>
        <form method="post" action="/allowlist/remove">
          <label>User ID<br><input type="text" name="user_id" required pattern="[0-9]+"></label>
          <button type="submit">Remove</button>
        </form>
        """
        return page("Allowlist", body)

    @app.post("/allowlist/add")
    async def allowlist_add(user_id: str = Form(...), _: None = Depends(require_auth)) -> RedirectResponse:
        db.add_allowed(int(user_id.strip()))
        return RedirectResponse("/allowlist?saved=1", status_code=303)

    @app.post("/allowlist/remove")
    async def allowlist_remove(user_id: str = Form(...), _: None = Depends(require_auth)) -> RedirectResponse:
        try:
            db.remove_allowed(int(user_id.strip()))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/allowlist?saved=1", status_code=303)

    @app.get("/agents", response_class=HTMLResponse)
    async def agents_page(_: None = Depends(require_auth)) -> HTMLResponse:
        rows_html = []
        for row in db.list_agent_rows():
            aid = html.escape(str(row["id"]))
            rows_html.append(
                "<tr>"
                f"<td><code>{aid}</code></td>"
                f"<td>{html.escape(str(row['display_name']))}</td>"
                f"<td>{'yes' if row['is_manager'] else ''}</td>"
                f"<td>{html.escape(str(row.get('default_backend') or 'cursor-cli'))}</td>"
                f"<td>{'✓' if row['discord_enabled'] else ''}</td>"
                f"<td>{html.escape(str(row['discord_channel_id'] or ''))}</td>"
                f"<td><code>{html.escape(str(row['discord_token_env'] or ''))}</code></td>"
                f"<td>{'✓' if row['telegram_enabled'] else ''}</td>"
                f"<td><a href='/agents/{aid}'>Edit</a></td>"
                "</tr>"
            )
        body = f"""
        <p>Agent profiles live in SQLite. Bot <strong>tokens</strong> stay in <code>.env</code>
        (referenced by <code>token_env</code>). Backend <code>openrouter</code> needs
        <code>OPENROUTER_API_KEY</code> (chat-only). Backend <code>claude-cli</code> needs
        the <code>claude</code> binary logged in on the host (Pro/Max).</p>
        <table>
          <tr><th>ID</th><th>Name</th><th>Manager</th><th>Backend</th><th>Discord</th><th>Channel</th><th>Token env</th><th>Telegram</th><th></th></tr>
          {''.join(rows_html) or '<tr><td colspan=9><em>none</em></td></tr>'}
        </table>
        <p><a href="/agents/new">Add agent</a></p>
        """
        return page("Agents", body)

    def _agent_form(row: dict[str, Any] | None, action: str) -> str:
        r = row or {}
        skills = r.get("skills") or "[]"
        if isinstance(skills, str) and skills.startswith("["):
            import json

            try:
                skills = "\n".join(json.loads(skills))
            except Exception:
                pass
        checked = lambda key: "checked" if r.get(key) else ""
        return f"""
        <form method="post" action="{action}">
          <label>ID<br><input type="text" name="id" value="{html.escape(str(r.get('id') or ''))}" required></label>
          <label>Display name<br><input type="text" name="display_name" value="{html.escape(str(r.get('display_name') or ''))}" required></label>
          <label>Workspace<br><input type="text" name="workspace" value="{html.escape(str(r.get('workspace') or '/home/maxi'))}"></label>
          <label>Default backend (<code>cursor-cli</code>, <code>claude-cli</code>, or <code>openrouter</code>)<br>
            <input type="text" name="default_backend" value="{html.escape(str(r.get('default_backend') or 'cursor-cli'))}"></label>
          <label>Skills (one path per line)<br><textarea name="skills">{html.escape(str(skills))}</textarea></label>
          <label>System prompt file<br><input type="text" name="system_prompt_file" value="{html.escape(str(r.get('system_prompt_file') or ''))}"></label>
          <label><input type="checkbox" name="is_manager" {checked('is_manager')}> Manager</label>
          <h3>Discord</h3>
          <label><input type="checkbox" name="discord_enabled" {checked('discord_enabled')}> Enabled</label>
          <label>Token env<br><input type="text" name="discord_token_env" value="{html.escape(str(r.get('discord_token_env') or ''))}"></label>
          <label>Channel ID<br><input type="text" name="discord_channel_id" value="{html.escape(str(r.get('discord_channel_id') or ''))}"></label>
          <h3>Telegram</h3>
          <p class="warn">Optional — leave disabled until you create bots via @BotFather,
          add <code>TELEGRAM_TOKEN_*</code> to <code>.env</code>, add your Telegram user ID to
          Allowlist, then enable and restart the service.</p>
          <label><input type="checkbox" name="telegram_enabled" {checked('telegram_enabled')}> Enabled</label>
          <label>Token env<br><input type="text" name="telegram_token_env" value="{html.escape(str(r.get('telegram_token_env') or ''))}" placeholder="TELEGRAM_TOKEN_MANAGER"></label>
          <label>Chat ID (optional — restrict to one group/DM)<br><input type="text" name="telegram_chat_id" value="{html.escape(str(r.get('telegram_chat_id') or ''))}"></label>
          <p><button type="submit">Save</button></p>
        </form>
        """

    @app.get("/agents/new", response_class=HTMLResponse)
    async def agent_new(_: None = Depends(require_auth)) -> HTMLResponse:
        return page("New agent", _agent_form(None, "/agents/save"))

    @app.get("/agents/{agent_id}", response_class=HTMLResponse)
    async def agent_edit(agent_id: str, _: None = Depends(require_auth)) -> HTMLResponse:
        row = db.get_agent_row(agent_id)
        if not row:
            raise HTTPException(404, "Unknown agent")
        return page(f"Edit {agent_id}", _agent_form(row, "/agents/save"))

    @app.post("/agents/save")
    async def agent_save(
        id: str = Form(...),
        display_name: str = Form(...),
        workspace: str = Form("/home/maxi"),
        default_backend: str = Form("cursor-cli"),
        skills: str = Form(""),
        system_prompt_file: str = Form(""),
        is_manager: str | None = Form(None),
        discord_enabled: str | None = Form(None),
        discord_token_env: str = Form(""),
        discord_channel_id: str = Form(""),
        telegram_enabled: str | None = Form(None),
        telegram_token_env: str = Form(""),
        telegram_chat_id: str = Form(""),
        _: None = Depends(require_auth),
    ) -> RedirectResponse:
        skill_list = [s.strip() for s in skills.splitlines() if s.strip()]
        db.upsert_agent(
            {
                "id": id.strip(),
                "display_name": display_name.strip(),
                "workspace": workspace.strip() or "/home/maxi",
                "default_backend": default_backend.strip() or "cursor-cli",
                "skills": skill_list,
                "system_prompt_file": system_prompt_file.strip() or None,
                "is_manager": is_manager is not None,
                "discord_enabled": discord_enabled is not None,
                "discord_token_env": discord_token_env.strip() or None,
                "discord_channel_id": discord_channel_id.strip() or None,
                "telegram_enabled": telegram_enabled is not None,
                "telegram_token_env": telegram_token_env.strip() or None,
                "telegram_chat_id": telegram_chat_id.strip() or None,
            }
        )
        return RedirectResponse("/agents?saved=1", status_code=303)

    @app.get("/sessions", response_class=HTMLResponse)
    async def sessions_page(_: None = Depends(require_auth)) -> HTMLResponse:
        data = db.list_sessions()
        rows = []
        for key, row in data.items():
            sid = row.get("session_id") or ""
            rows.append(
                f"<tr><td><code>{html.escape(key)}</code></td>"
                f"<td>{html.escape(row.get('title') or '')}</td>"
                f"<td><code>{html.escape(sid[:16])}{'…' if len(sid) > 16 else ''}</code></td>"
                f"<td><form method='post' action='/sessions/clear' style='margin:0'>"
                f"<input type='hidden' name='key' value='{html.escape(key)}'>"
                f"<button type='submit'>Clear</button></form></td></tr>"
            )
        body = f"""
        <p>Thread ↔ Cursor <code>--resume</code> / OpenRouter session IDs (SQLite).</p>
        <table>
          <tr><th>Key</th><th>Title</th><th>Session</th><th></th></tr>
          {''.join(rows) or '<tr><td colspan=4><em>none</em></td></tr>'}
        </table>
        """
        return page("Sessions", body)

    @app.post("/sessions/clear")
    async def sessions_clear(key: str = Form(...), _: None = Depends(require_auth)) -> RedirectResponse:
        db.clear_session(key)
        return RedirectResponse("/sessions", status_code=303)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        out: dict[str, Any] = {"status": "ok"}
        if gateway is not None:
            st = gateway.live_status()
            out["running"] = st["running_count"]
            out["queued_threads"] = st["queued_threads"]
            out["rebuild_pending"] = st["rebuild_pending"]
        return out

    return app
