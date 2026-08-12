from __future__ import annotations

import html
import json
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
:root {
  --bg: #f4f6f8;
  --surface: #ffffff;
  --text: #1a2332;
  --muted: #5c6b7a;
  --border: #d8dee6;
  --accent: #0f766e;
  --accent-soft: #ccfbf1;
  --ok: #ecfdf5;
  --ok-border: #a7f3d0;
  --warn-bg: #fff7ed;
  --warn-border: #fed7aa;
  --err-bg: #fef2f2;
  --err-border: #fecaca;
  --radius: 10px;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.06);
  --font: "Segoe UI", system-ui, -apple-system, sans-serif;
  --mono: ui-monospace, "Cascadia Code", "SF Mono", Menlo, monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: var(--font);
  color: var(--text);
  background: var(--bg);
  line-height: 1.45;
  -webkit-text-size-adjust: 100%;
}
a { color: var(--accent); }
.wrap { max-width: 1040px; margin: 0 auto; padding: 1rem 1rem 2.5rem; }
.top {
  display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
  gap: 0.75rem 1.25rem; margin-bottom: 1.25rem; padding-bottom: 1rem;
  border-bottom: 1px solid var(--border);
}
.brand { min-width: 0; }
.brand h1 {
  margin: 0; font-size: 1.35rem; letter-spacing: -0.02em; font-weight: 700;
}
.brand p { margin: 0.2rem 0 0; color: var(--muted); font-size: 0.9rem; }
nav {
  display: flex; flex-wrap: nowrap; gap: 0.35rem; overflow-x: auto;
  -webkit-overflow-scrolling: touch; padding-bottom: 0.15rem; max-width: 100%;
}
nav a {
  flex: 0 0 auto; text-decoration: none; color: var(--muted);
  padding: 0.45rem 0.75rem; border-radius: 999px; font-size: 0.9rem; font-weight: 550;
  background: transparent; border: 1px solid transparent;
}
nav a:hover { color: var(--text); background: var(--surface); border-color: var(--border); }
nav a.active {
  color: var(--accent); background: var(--accent-soft); border-color: #99f6e4;
}
.page-title { margin: 0 0 1rem; font-size: 1.2rem; }
.msg, .callout {
  padding: 0.75rem 0.9rem; border-radius: var(--radius); margin-bottom: 1rem;
  border: 1px solid var(--ok-border); background: var(--ok); box-shadow: var(--shadow);
}
.callout.warn, .msg.warn, .warn {
  background: var(--warn-bg); border-color: var(--warn-border);
}
.callout.err, .msg.err, .err {
  background: var(--err-bg); border-color: var(--err-border);
}
.muted { color: var(--muted); font-size: 0.9rem; }
.cards {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem; margin: 1rem 0 1.25rem;
}
.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 0.85rem 1rem; box-shadow: var(--shadow);
}
.card .label { display: block; color: var(--muted); font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.04em; margin-bottom: 0.25rem; }
.card .value { font-size: 1.45rem; font-weight: 700; letter-spacing: -0.02em; }
.card a { text-decoration: none; color: inherit; }
.badges { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.75rem 0; }
.badge {
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 0.25rem 0.6rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600;
  background: var(--surface); border: 1px solid var(--border); color: var(--muted);
}
.badge.on { color: var(--accent); background: var(--accent-soft); border-color: #99f6e4; }
.badge.warn { color: #9a3412; background: var(--warn-bg); border-color: var(--warn-border); }
.badge.err { color: #991b1b; background: var(--err-bg); border-color: var(--err-border); }
.table-wrap {
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface); box-shadow: var(--shadow); margin: 0.75rem 0 1.25rem;
}
table { border-collapse: collapse; width: 100%; min-width: 480px; }
th, td {
  border-bottom: 1px solid var(--border); padding: 0.55rem 0.7rem;
  text-align: left; vertical-align: top; font-size: 0.92rem;
}
th {
  background: #f8fafc; color: var(--muted); font-size: 0.75rem;
  text-transform: uppercase; letter-spacing: 0.04em; font-weight: 650;
}
tr:last-child td { border-bottom: none; }
code, pre {
  font-family: var(--mono); font-size: 0.84em;
  background: #f1f5f9; padding: 0.12rem 0.35rem; border-radius: 4px;
}
pre {
  display: block; padding: 0.85rem 1rem; overflow-x: auto; white-space: pre-wrap;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface);
}
label { display: block; margin: 0.75rem 0; font-weight: 550; font-size: 0.92rem; }
input[type=text], textarea {
  display: block; width: 100%; max-width: 36rem; margin-top: 0.35rem;
  padding: 0.55rem 0.7rem; border: 1px solid var(--border); border-radius: 8px;
  font: inherit; background: var(--surface);
}
textarea { min-height: 5rem; resize: vertical; }
button, .btn {
  display: inline-block; padding: 0.55rem 0.95rem; cursor: pointer; font: inherit;
  font-weight: 600; font-size: 0.9rem; border-radius: 8px;
  border: 1px solid var(--accent); background: var(--accent); color: #fff;
  text-decoration: none;
}
button.secondary, .btn.secondary {
  background: var(--surface); color: var(--text); border-color: var(--border);
}
button.danger {
  background: #fff; color: #b91c1c; border-color: #fecaca;
}
button:hover, .btn:hover { filter: brightness(0.97); }
.row-actions { display: flex; gap: 0.4rem; align-items: center; }
.job-cards { display: none; gap: 0.75rem; margin: 0.75rem 0 1.25rem; }
.job-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 0.85rem 1rem; box-shadow: var(--shadow);
}
.job-card .meta { color: var(--muted); font-size: 0.85rem; margin-top: 0.35rem; }
.section { margin: 1.5rem 0 0.5rem; font-size: 1.05rem; }
.hint { color: var(--muted); font-size: 0.9rem; margin: 0.35rem 0 1rem; }
@media (max-width: 700px) {
  .wrap { padding: 0.85rem 0.85rem 2rem; }
  input[type=text], textarea { max-width: none; }
  .desk-only { display: none !important; }
  .job-cards { display: grid; }
  table { min-width: 420px; }
}
@media (min-width: 701px) {
  .mobile-only { display: none !important; }
}
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


def _short_key(key: str, keep: int = 28) -> str:
    if len(key) <= keep:
        return key
    return key[: keep - 1] + "…"


def create_admin_app(*, db: ConfigStore, gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="zen-agent-bot admin", docs_url=None, redoc_url=None)

    def page(
        title: str,
        body: str,
        msg: str = "",
        *,
        active: str = "",
        refresh: int | None = None,
        banner_class: str = "",
    ) -> HTMLResponse:
        links = [
            ("/", "Dashboard", "dashboard"),
            ("/status", "Status", "status"),
            ("/allowlist", "Allowlist", "allowlist"),
            ("/agents", "Agents", "agents"),
            ("/sessions", "Sessions", "sessions"),
        ]
        nav_parts = []
        for href, label, key in links:
            cls = "active" if active == key else ""
            nav_parts.append(f'<a class="{cls}" href="{href}">{label}</a>')
        nav = f"<nav>{''.join(nav_parts)}</nav>"
        banner = ""
        if msg:
            cls = f"msg {banner_class}".strip()
            banner = f'<div class="{cls}">{msg}</div>'
        meta_refresh = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
        html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {meta_refresh}
  <title>{html.escape(title)} · Zen Agents</title>
  <style>{BASE_STYLE}</style>
</head>
<body>
  <div class="wrap">
    <header class="top">
      <div class="brand">
        <h1>Zen Agents</h1>
        <p>Gateway admin · Discord / Telegram</p>
      </div>
      {nav}
    </header>
    {banner}
    <h2 class="page-title">{html.escape(title)}</h2>
    {body}
  </div>
</body>
</html>"""
        return HTMLResponse(html_out)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(
        request: Request, _: None = Depends(require_auth)
    ) -> HTMLResponse:
        agents = db.list_agent_rows()
        allowed = db.allowlist()
        sessions = db.list_sessions()
        tg_ready = sum(1 for a in agents if a.get("telegram_token_env"))
        pw = _admin_password()
        running = queued = 0
        if gateway is not None:
            st = gateway.live_status()
            running = st["running_count"]
            queued = st["queued_threads"]

        auth_banner = (
            ('Auth protected (ADMIN_PASSWORD set)', "")
            if pw
            else (
                "No ADMIN_PASSWORD — set one in <code>.env</code> then restart.",
                "warn",
            )
        )
        msg = ""
        banner_class = ""
        if request.query_params.get("saved"):
            msg = "Saved."
        if not pw:
            msg = auth_banner[0]
            banner_class = auth_banner[1]

        body = f"""
        <div class="cards">
          <div class="card"><span class="label">Agents</span>
            <div class="value"><a href="/agents">{len(agents)}</a></div></div>
          <div class="card"><span class="label">Allowlist</span>
            <div class="value"><a href="/allowlist">{len(allowed)}</a></div></div>
          <div class="card"><span class="label">Sessions</span>
            <div class="value"><a href="/sessions">{len(sessions)}</a></div></div>
          <div class="card"><span class="label">Running</span>
            <div class="value"><a href="/status">{running}</a></div></div>
          <div class="card"><span class="label">Queued threads</span>
            <div class="value"><a href="/status">{queued}</a></div></div>
          <div class="card"><span class="label">Telegram-ready</span>
            <div class="value">{tg_ready}</div></div>
        </div>
        <p class="hint">DB <code>{html.escape(str(db.path))}</code></p>
        <div class="callout">
          <strong>Live vs restart</strong><br>
          Allowlist and session clears apply immediately.
          New bots / token or channel changes need a host restart
          (<code>systemctl restart zen-agent-bot</code> or Discord <code>/rebuild</code>).
        </div>
        """
        return page("Dashboard", body, msg, active="dashboard", banner_class=banner_class)

    @app.get("/status", response_class=HTMLResponse)
    async def status_page(_: None = Depends(require_auth)) -> HTMLResponse:
        if gateway is None:
            return page(
                "Status",
                "<div class='callout warn'>Gateway not attached to admin app.</div>",
                active="status",
            )

        st = gateway.live_status()
        agent_login = await gateway.cursor_agent_status()

        run_rows: list[str] = []
        job_cards: list[str] = []
        for job in st["running"]:
            key = str(job["session_key"])
            agent = str(job["agent_id"])
            preview = str(job["prompt_preview"])
            run_rows.append(
                "<tr>"
                f"<td><code title=\"{html.escape(key)}\">{html.escape(_short_key(key))}</code></td>"
                f"<td>{html.escape(agent)}</td>"
                f"<td>{job['elapsed_sec']}s</td>"
                f"<td>{html.escape(preview)}</td>"
                f"<td>{job['queue_behind']}</td>"
                f"<td>{job['pid'] or '—'}</td>"
                "</tr>"
            )
            job_cards.append(
                f"""<div class="job-card">
                  <strong>{html.escape(agent)}</strong> · {job['elapsed_sec']}s
                  <div>{html.escape(preview)}</div>
                  <div class="meta">
                    <code title="{html.escape(key)}">{html.escape(_short_key(key, 36))}</code>
                    · queue behind {job['queue_behind']} · pid {job['pid'] or '—'}
                  </div>
                </div>"""
            )

        q_rows = []
        for item in st["queued"]:
            key = str(item["session_key"])
            q_rows.append(
                f"<tr><td><code title=\"{html.escape(key)}\">{html.escape(_short_key(key))}</code></td>"
                f"<td>{item['queued']}</td></tr>"
            )
        err_rows = []
        for err in st["last_errors"][:10]:
            key = str(err["session_key"])
            err_rows.append(
                "<tr>"
                f"<td>{html.escape(_fmt_ts(err['at']))}</td>"
                f"<td>{html.escape(str(err['agent_id']))}</td>"
                f"<td><code title=\"{html.escape(key)}\">{html.escape(_short_key(key))}</code></td>"
                f"<td>{html.escape(err['error'])}</td>"
                "</tr>"
            )

        badges = [
            f'<span class="badge {"on" if st["running_count"] else ""}">Running {st["running_count"]}</span>',
            f'<span class="badge {"on" if st["queued_threads"] else ""}">Queued {st["queued_threads"]}</span>',
            f'<span class="badge {"on" if st["streaming_enabled"] else ""}">Streaming {"on" if st["streaming_enabled"] else "off"}</span>',
            f'<span class="badge">Max {st["max_concurrent_jobs"]}</span>',
        ]
        if st["shutting_down"]:
            badges.append('<span class="badge err">Shutting down</span>')
        if st["rebuild_pending"]:
            badges.append('<span class="badge warn">Rebuild pending</span>')

        body = f"""
        <p class="muted">Auto-refresh 5s · <a href="/api/status">JSON</a></p>
        <div class="badges">{"".join(badges)}</div>
        <p class="hint">Backends: <code>{html.escape(", ".join(st["backends"]))}</code></p>

        <h3 class="section">Cursor agent login</h3>
        <pre>{html.escape(agent_login)}</pre>

        <h3 class="section">Running jobs ({st['running_count']})</h3>
        <div class="job-cards mobile-only">
          {"".join(job_cards) or '<p class="muted">None running.</p>'}
        </div>
        <div class="table-wrap desk-only">
          <table>
            <tr><th>Session</th><th>Agent</th><th>Elapsed</th><th>Prompt</th><th>Queued</th><th>PID</th></tr>
            {"".join(run_rows) or "<tr><td colspan=6><em>none</em></td></tr>"}
          </table>
        </div>

        <h3 class="section">Queued threads ({st['queued_threads']})</h3>
        <div class="table-wrap">
          <table>
            <tr><th>Session</th><th>Queued</th></tr>
            {"".join(q_rows) or "<tr><td colspan=2><em>none</em></td></tr>"}
          </table>
        </div>

        <h3 class="section">Last errors</h3>
        <div class="table-wrap">
          <table>
            <tr><th>When</th><th>Agent</th><th>Session</th><th>Error</th></tr>
            {"".join(err_rows) or "<tr><td colspan=4><em>none</em></td></tr>"}
          </table>
        </div>
        """
        return page("Status", body, active="status", refresh=5)

    @app.get("/api/status")
    async def api_status(_: None = Depends(require_auth)) -> JSONResponse:
        if gateway is None:
            return JSONResponse({"error": "gateway not attached"}, status_code=503)
        payload = gateway.live_status()
        payload["cursor_agent_status"] = await gateway.cursor_agent_status()
        payload["ts"] = time.time()
        return JSONResponse(payload)

    @app.get("/allowlist", response_class=HTMLResponse)
    async def allowlist_get(
        request: Request, _: None = Depends(require_auth)
    ) -> HTMLResponse:
        ids = db.allowlist()
        rows = []
        for uid in ids:
            rows.append(
                f"""<tr>
                  <td><code>{uid}</code></td>
                  <td>
                    <form method="post" action="/allowlist/remove" class="row-actions">
                      <input type="hidden" name="user_id" value="{uid}">
                      <button type="submit" class="danger">Remove</button>
                    </form>
                  </td>
                </tr>"""
            )
        msg = "Saved." if request.query_params.get("saved") else ""
        body = f"""
        <p class="hint">Only these Discord/Telegram user IDs can message the bots. Changes apply immediately.</p>
        <div class="table-wrap">
          <table>
            <tr><th>User ID</th><th></th></tr>
            {"".join(rows) or "<tr><td colspan=2><em>empty</em></td></tr>"}
          </table>
        </div>
        <h3 class="section">Add user</h3>
        <form method="post" action="/allowlist/add">
          <label>User ID
            <input type="text" name="user_id" required pattern="[0-9]+" inputmode="numeric" placeholder="Discord snowflake">
          </label>
          <button type="submit">Add</button>
        </form>
        """
        return page("Allowlist", body, msg, active="allowlist")

    @app.post("/allowlist/add")
    async def allowlist_add(
        user_id: str = Form(...), _: None = Depends(require_auth)
    ) -> RedirectResponse:
        db.add_allowed(int(user_id.strip()))
        return RedirectResponse("/allowlist?saved=1", status_code=303)

    @app.post("/allowlist/remove")
    async def allowlist_remove(
        user_id: str = Form(...), _: None = Depends(require_auth)
    ) -> RedirectResponse:
        try:
            db.remove_allowed(int(user_id.strip()))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/allowlist?saved=1", status_code=303)

    @app.get("/agents", response_class=HTMLResponse)
    async def agents_page(
        request: Request, _: None = Depends(require_auth)
    ) -> HTMLResponse:
        rows_html = []
        for row in db.list_agent_rows():
            aid = html.escape(str(row["id"]))
            rows_html.append(
                "<tr>"
                f"<td><code>{aid}</code></td>"
                f"<td>{html.escape(str(row['display_name']))}</td>"
                f"<td>{'yes' if row['is_manager'] else '—'}</td>"
                f"<td>{html.escape(str(row.get('default_backend') or 'cursor-cli'))}</td>"
                f"<td>{'✓' if row['discord_enabled'] else '—'}</td>"
                f"<td>{html.escape(str(row['discord_channel_id'] or '—'))}</td>"
                f"<td><code>{html.escape(str(row['discord_token_env'] or '—'))}</code></td>"
                f"<td>{'✓' if row['telegram_enabled'] else '—'}</td>"
                f"<td><a class='btn secondary' href='/agents/{aid}'>Edit</a></td>"
                "</tr>"
            )
        msg = "Saved." if request.query_params.get("saved") else ""
        body = f"""
        <p class="hint">Profiles in SQLite. Tokens stay in <code>.env</code> (via <code>token_env</code>).
        <code>openrouter</code> needs <code>OPENROUTER_API_KEY</code>; <code>claude-cli</code> needs host <code>claude</code> login.</p>
        <p><a class="btn" href="/agents/new">Add agent</a></p>
        <div class="table-wrap">
          <table>
            <tr><th>ID</th><th>Name</th><th>Mgr</th><th>Backend</th><th>Discord</th><th>Channel</th><th>Token env</th><th>TG</th><th></th></tr>
            {"".join(rows_html) or "<tr><td colspan=9><em>none</em></td></tr>"}
          </table>
        </div>
        """
        return page("Agents", body, msg, active="agents")

    def _agent_form(row: dict[str, Any] | None, action: str) -> str:
        r = row or {}
        skills = r.get("skills") or "[]"
        if isinstance(skills, str) and skills.startswith("["):
            try:
                skills = "\n".join(json.loads(skills))
            except Exception:
                pass
        checked = lambda key: "checked" if r.get(key) else ""
        return f"""
        <form method="post" action="{action}">
          <label>ID
            <input type="text" name="id" value="{html.escape(str(r.get('id') or ''))}" required></label>
          <label>Display name
            <input type="text" name="display_name" value="{html.escape(str(r.get('display_name') or ''))}" required></label>
          <label>Workspace
            <input type="text" name="workspace" value="{html.escape(str(r.get('workspace') or '/home/maxi'))}"></label>
          <label>Default backend (<code>cursor-cli</code>, <code>claude-cli</code>, <code>openrouter</code>)
            <input type="text" name="default_backend" value="{html.escape(str(r.get('default_backend') or 'cursor-cli'))}"></label>
          <label>Skills (one path per line)
            <textarea name="skills">{html.escape(str(skills))}</textarea></label>
          <label>System prompt file
            <input type="text" name="system_prompt_file" value="{html.escape(str(r.get('system_prompt_file') or ''))}"></label>
          <label><input type="checkbox" name="is_manager" {checked('is_manager')}> Manager</label>

          <h3 class="section">Discord</h3>
          <label><input type="checkbox" name="discord_enabled" {checked('discord_enabled')}> Enabled</label>
          <label>Token env
            <input type="text" name="discord_token_env" value="{html.escape(str(r.get('discord_token_env') or ''))}"></label>
          <label>Channel ID
            <input type="text" name="discord_channel_id" value="{html.escape(str(r.get('discord_channel_id') or ''))}"></label>

          <h3 class="section">Telegram</h3>
          <div class="callout warn">
            Optional — create bots via @BotFather, add <code>TELEGRAM_TOKEN_*</code> to
            <code>.env</code>, allowlist your Telegram user ID, enable here, then restart.
          </div>
          <label><input type="checkbox" name="telegram_enabled" {checked('telegram_enabled')}> Enabled</label>
          <label>Token env
            <input type="text" name="telegram_token_env" value="{html.escape(str(r.get('telegram_token_env') or ''))}" placeholder="TELEGRAM_TOKEN_MANAGER"></label>
          <label>Chat ID (optional — restrict to one group/DM)
            <input type="text" name="telegram_chat_id" value="{html.escape(str(r.get('telegram_chat_id') or ''))}"></label>
          <p><button type="submit">Save</button>
             <a class="btn secondary" href="/agents">Cancel</a></p>
        </form>
        """

    @app.get("/agents/new", response_class=HTMLResponse)
    async def agent_new(_: None = Depends(require_auth)) -> HTMLResponse:
        return page("New agent", _agent_form(None, "/agents/save"), active="agents")

    @app.get("/agents/{agent_id}", response_class=HTMLResponse)
    async def agent_edit(agent_id: str, _: None = Depends(require_auth)) -> HTMLResponse:
        row = db.get_agent_row(agent_id)
        if not row:
            raise HTTPException(404, "Unknown agent")
        return page(
            f"Edit {agent_id}", _agent_form(row, "/agents/save"), active="agents"
        )

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
                f"<tr><td><code title=\"{html.escape(key)}\">{html.escape(_short_key(key, 40))}</code></td>"
                f"<td>{html.escape(row.get('title') or '')}</td>"
                f"<td><code>{html.escape(sid[:16])}{'…' if len(sid) > 16 else ''}</code></td>"
                f"<td><form method='post' action='/sessions/clear' class='row-actions'>"
                f"<input type='hidden' name='key' value='{html.escape(key)}'>"
                f"<button type='submit' class='danger'>Clear</button></form></td></tr>"
            )
        body = f"""
        <p class="hint">Thread ↔ Cursor <code>--resume</code> / OpenRouter session IDs (SQLite).</p>
        <div class="table-wrap">
          <table>
            <tr><th>Key</th><th>Title</th><th>Session</th><th></th></tr>
            {"".join(rows) or "<tr><td colspan=4><em>none</em></td></tr>"}
          </table>
        </div>
        """
        return page("Sessions", body, active="sessions")

    @app.post("/sessions/clear")
    async def sessions_clear(
        key: str = Form(...), _: None = Depends(require_auth)
    ) -> RedirectResponse:
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
