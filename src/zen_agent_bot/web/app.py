from __future__ import annotations

import html
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from ..schedule import (
    DEFAULT_TZ,
    MAX_NAME,
    MAX_PROMPT,
    next_run_iso,
    slug_id,
    validate_cron,
    validate_timezone,
)
from ..store import ConfigStore

STATIC_DIR = Path(__file__).resolve().parent / "static"

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
html, body { max-width: 100%; overflow-x: hidden; }
body {
  margin: 0;
  font-family: var(--font);
  color: var(--text);
  background: var(--bg);
  line-height: 1.45;
  -webkit-text-size-adjust: 100%;
}
a { color: var(--accent); }
.wrap {
  width: 100%; max-width: 1040px; margin: 0 auto;
  padding: 1rem 1rem 2.5rem; overflow-x: clip;
}
.top {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 0.75rem; margin-bottom: 1.25rem; padding-bottom: 1rem;
  border-bottom: 1px solid var(--border);
}
.brand { min-width: 0; flex: 1 1 auto; }
.brand h1 {
  margin: 0; font-size: 1.35rem; letter-spacing: -0.02em; font-weight: 700;
}
.brand p { margin: 0.2rem 0 0; color: var(--muted); font-size: 0.9rem; }
nav {
  display: flex; flex-wrap: wrap; gap: 0.35rem; max-width: 100%;
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
  display: grid; grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
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
table { border-collapse: collapse; width: 100%; min-width: 0; }
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
input[type=text], input[type=password], textarea, select {
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
.row-actions { display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
.job-cards { display: none; gap: 0.75rem; margin: 0.75rem 0 1.25rem; }
.job-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 0.85rem 1rem; box-shadow: var(--shadow);
}
.job-card .meta { color: var(--muted); font-size: 0.85rem; margin-top: 0.35rem; }
.section { margin: 1.5rem 0 0.5rem; font-size: 1.05rem; }
.hint { color: var(--muted); font-size: 0.9rem; margin: 0.35rem 0 1rem; }
.header-actions {
  display: flex; align-items: center; gap: 0.45rem; flex-wrap: wrap;
  justify-content: flex-start; min-width: 0; max-width: 100%;
}
.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 2.4rem; height: 2.4rem; padding: 0; border-radius: 8px; flex: 0 0 auto;
  border: 1px solid var(--border); background: var(--surface); color: var(--text);
  cursor: pointer; box-shadow: var(--shadow);
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); }
.icon-btn svg { width: 1.15rem; height: 1.15rem; display: block; }
.settings-backdrop {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.35);
  opacity: 0; pointer-events: none; transition: opacity 0.2s ease; z-index: 40;
}
.settings-panel {
  position: fixed; top: 0; right: 0; height: 100%; width: min(26rem, 100vw);
  max-width: 100%; background: var(--surface); border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(16, 24, 40, 0.12);
  transform: translateX(100%); transition: transform 0.22s ease;
  z-index: 50; display: flex; flex-direction: column;
}
body.settings-open { overflow: hidden; }
body.settings-open .settings-backdrop { opacity: 1; pointer-events: auto; }
body.settings-open .settings-panel { transform: translateX(0); }
.settings-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 0.75rem; padding: 1rem 1.1rem; border-bottom: 1px solid var(--border);
}
.settings-head h2 { margin: 0; font-size: 1.1rem; }
.settings-body { padding: 1rem 1.1rem 2rem; overflow-y: auto; flex: 1; }
.settings-body .section { margin-top: 1.25rem; }
.settings-body label input[type=checkbox] { width: auto; margin-right: 0.45rem; }
.settings-links { display: grid; gap: 0.5rem; margin: 0.5rem 0 0; }
.settings-links a {
  display: block; padding: 0.7rem 0.85rem; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg); text-decoration: none;
  color: var(--text); font-weight: 600;
}
.settings-links a:hover { border-color: var(--accent); color: var(--accent); }
.settings-links a span { display: block; color: var(--muted); font-weight: 500; font-size: 0.82rem; margin-top: 0.15rem; }
@media (max-width: 700px) {
  .wrap { padding: 0.85rem max(0.85rem, env(safe-area-inset-right)) 2rem max(0.85rem, env(safe-area-inset-left)); }
  .brand h1 { font-size: 1.2rem; }
  .brand p { font-size: 0.82rem; }
  .header-actions { width: 100%; }
  nav { width: 100%; }
  .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  input[type=text], input[type=password], textarea { max-width: none; }
  .desk-only { display: none !important; }
  .job-cards { display: grid; }
  table { min-width: 0; }
  .table-wrap { margin-left: 0; margin-right: 0; }
}
@media (min-width: 701px) {
  .mobile-only { display: none !important; }
  .header-actions { justify-content: flex-end; flex: 1 1 auto; }
  table { min-width: 420px; }
}
"""


WELL_KNOWN_SECRETS = (
    "OPENROUTER_API_KEY",
    "CURSOR_API_KEY",
    "ADMIN_PASSWORD",
    "DISCORD_TOKEN_MANAGER",
    "DISCORD_TOKEN_MUSIC",
    "DISCORD_TOKEN_GENERAL",
    "TELEGRAM_TOKEN_MANAGER",
    "TELEGRAM_TOKEN_MUSIC",
    "TELEGRAM_TOKEN_GENERAL",
)


def _admin_password(db: ConfigStore | None = None) -> str | None:
    if db is not None:
        return db.resolve_secret("ADMIN_PASSWORD") or None
    return os.environ.get("ADMIN_PASSWORD", "").strip() or None


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    db = getattr(request.app.state, "db", None)
    password = _admin_password(db if isinstance(db, ConfigStore) else None)
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


def _truthy_setting(raw: str | None, default: bool = True) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def create_admin_app(*, db: ConfigStore, gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="zen-agent-bot admin", docs_url=None, redoc_url=None)
    app.state.db = db
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _settings_panel(*, open_on_load: bool = False) -> str:
        streaming = _truthy_setting(db.get_setting("streaming_enabled"), True)
        if gateway is not None:
            streaming = gateway.config.streaming_enabled
        job_ping = _truthy_setting(db.get_setting("job_done_ping"), True)
        max_jobs = db.get_setting("max_concurrent_jobs", "2") or "2"
        if gateway is not None:
            max_jobs = str(gateway.config.max_concurrent_jobs)
        guild = (
            os.environ.get("DISCORD_GUILD_ID")
            or db.get_setting("discord_guild_id")
            or ""
        ).strip()
        cursor_model = db.get_setting("backend.cursor-cli.model") or ""
        cursor_sdk_model = db.get_setting("backend.cursor-sdk.model") or ""
        claude_model = db.get_setting("backend.claude-cli.model") or ""
        openrouter_model = db.get_setting("backend.openrouter.model") or ""
        openrouter_online = _truthy_setting(
            db.get_setting("backend.openrouter.online"), False
        )
        env_model_hits = [
            name
            for name in ("AGENT_MODEL", "CLAUDE_MODEL", "OPENROUTER_MODEL")
            if os.environ.get(name, "").strip()
        ]
        env_model_note = (
            f'<div class="callout warn">Env currently overrides admin models: '
            f'<code>{html.escape(", ".join(env_model_hits))}</code></div>'
            if env_model_hits
            else ""
        )
        pw = _admin_password(db)
        auth_html = (
            '<div class="callout">Auth protected (<code>ADMIN_PASSWORD</code> set).</div>'
            if pw
            else (
                '<div class="callout warn">No <code>ADMIN_PASSWORD</code> — set one under '
                "<a href=\"/secrets\">Secrets</a> (live) or in <code>.env</code>.</div>"
            )
        )
        streaming_checked = "checked" if streaming else ""
        job_ping_checked = "checked" if job_ping else ""
        openrouter_online_checked = "checked" if openrouter_online else ""
        open_script = (
            "document.body.classList.add('settings-open');" if open_on_load else ""
        )
        return f"""
  <div class="settings-backdrop" id="settings-backdrop" hidden></div>
  <aside class="settings-panel" id="settings-panel" aria-hidden="true" aria-label="Settings">
    <div class="settings-head">
      <h2>Settings</h2>
      <button type="button" class="icon-btn" id="settings-close" aria-label="Close settings">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18"/>
        </svg>
      </button>
    </div>
    <div class="settings-body">
      <h3 class="section">Manage</h3>
      <div class="settings-links">
        <a href="/agents">Agents<span>Profiles, Discord/Telegram tokens &amp; channels</span></a>
        <a href="/schedules">Schedules<span>Cron jobs — each run opens a Discord thread</span></a>
        <a href="/allowlist">Allowlist<span>Who can message the bots</span></a>
        <a href="/secrets">Secrets<span>API keys &amp; bot tokens (masked)</span></a>
      </div>

      <h3 class="section">Gateway</h3>
      <p class="hint">Saved to SQLite. Streaming, models, and OpenRouter <code>:online</code> apply live; concurrent jobs &amp; guild need a restart.</p>
      <form method="post" action="/settings/save">
        <label><input type="checkbox" name="streaming_enabled" {streaming_checked}> Discord/Telegram streaming status edits</label>
        <label><input type="checkbox" name="job_done_ping" {job_ping_checked}> Append @ you on the status bubble when a job finishes (successes ≥ 1 min, or errors)</label>
        <label>Max concurrent jobs
          <input type="text" name="max_concurrent_jobs" value="{html.escape(max_jobs)}" inputmode="numeric" pattern="[0-9]+" required>
        </label>
        <label>Discord guild ID (slash sync)
          <input type="text" name="discord_guild_id" value="{html.escape(guild)}" inputmode="numeric" pattern="[0-9]*" placeholder="optional">
        </label>
        <h3 class="section">Default models</h3>
        <p class="hint">Blank = CLI default (OpenRouter falls back to <code>anthropic/claude-sonnet-4</code>;
        cursor-sdk falls back to cursor-cli then <code>composer-2.5</code>).
        Thread <code>/model</code> overrides these. Env <code>AGENT_MODEL</code> / <code>CLAUDE_MODEL</code> /
        <code>OPENROUTER_MODEL</code> wins if set. Next job picks this up — no restart.</p>
        {env_model_note}
        <label>cursor-cli model
          <input type="text" name="cursor_model" value="{html.escape(cursor_model)}" placeholder="e.g. composer-2.5"></label>
        <label>cursor-sdk model
          <input type="text" name="cursor_sdk_model" value="{html.escape(cursor_sdk_model)}" placeholder="blank → cursor-cli / composer-2.5"></label>
        <label>claude-cli model
          <input type="text" name="claude_model" value="{html.escape(claude_model)}" placeholder="e.g. sonnet"></label>
        <label>openrouter model
          <input type="text" name="openrouter_model" value="{html.escape(openrouter_model)}" placeholder="e.g. anthropic/claude-sonnet-4"></label>
        <label><input type="checkbox" name="openrouter_online" {openrouter_online_checked}> OpenRouter web search (append <code>:online</code>; extra $). Next job — no restart. Env <code>OPENROUTER_ONLINE</code> wins if set. Skip if the model already has <code>:online</code>.</label>
        <p><button type="submit">Save settings</button></p>
      </form>

      <h3 class="section">Security</h3>
      {auth_html}

      <h3 class="section">Runtime</h3>
      <p class="hint">DB <code>{html.escape(str(db.path))}</code></p>
      <div class="callout">
        <strong>Live vs restart</strong><br>
        Allowlist, session clears, model defaults, OpenRouter key, and admin
        password apply immediately. Discord/Telegram <em>token values</em> need a
        host restart after saving
        (<code>systemctl restart zen-agent-bot</code> or Discord <code>/rebuild</code>).
      </div>
      <div class="callout">
        <strong>Install app</strong><br>
        On mobile, use the browser “Add to Home Screen” / Install for the Zen Agents PWA.
      </div>
    </div>
  </aside>
  <script>
    (function () {{
      var backdrop = document.getElementById("settings-backdrop");
      var panel = document.getElementById("settings-panel");
      var openBtn = document.getElementById("settings-open");
      var closeBtn = document.getElementById("settings-close");
      function openSettings() {{
        document.body.classList.add("settings-open");
        backdrop.hidden = false;
        panel.setAttribute("aria-hidden", "false");
      }}
      function closeSettings() {{
        document.body.classList.remove("settings-open");
        backdrop.hidden = true;
        panel.setAttribute("aria-hidden", "true");
      }}
      if (openBtn) openBtn.addEventListener("click", openSettings);
      if (closeBtn) closeBtn.addEventListener("click", closeSettings);
      if (backdrop) backdrop.addEventListener("click", closeSettings);
      document.addEventListener("keydown", function (e) {{
        if (e.key === "Escape") closeSettings();
      }});
      {open_script}
      if ("serviceWorker" in navigator) {{
        navigator.serviceWorker.register("/static/sw.js").catch(function () {{}});
      }}
    }})();
  </script>
"""

    def page(
        title: str,
        body: str,
        msg: str = "",
        *,
        active: str = "",
        refresh: int | None = None,
        banner_class: str = "",
        open_settings: bool = False,
    ) -> HTMLResponse:
        links = [
            ("/", "Dashboard", "dashboard"),
            ("/status", "Status", "status"),
            ("/schedules", "Schedules", "schedules"),
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
        settings = _settings_panel(open_on_load=open_settings)
        html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#0f766e">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="Zen Agents">
  <link rel="manifest" href="/static/manifest.webmanifest">
  <link rel="icon" href="/static/icon-192.png" sizes="192x192" type="image/png">
  <link rel="apple-touch-icon" href="/static/icon-192.png">
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
      <div class="header-actions">
        {nav}
        <button type="button" class="icon-btn" id="settings-open" aria-label="Open settings" title="Settings">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <circle cx="12" cy="12" r="3"/>
            <path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>
          </svg>
        </button>
      </div>
    </header>
    {banner}
    <h2 class="page-title">{html.escape(title)}</h2>
    {body}
  </div>
  {settings}
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
        schedules = db.list_schedules()
        tg_ready = sum(1 for a in agents if a.get("telegram_token_env"))
        pw = _admin_password(db)
        running = queued = 0
        if gateway is not None:
            st = gateway.live_status()
            running = st["running_count"]
            queued = st["queued_threads"]

        auth_banner = (
            ('Auth protected (ADMIN_PASSWORD set)', "")
            if pw
            else (
                'No ADMIN_PASSWORD — set one under <a href="/secrets">Secrets</a>.',
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

        open_settings = request.query_params.get("settings") == "1"
        if request.query_params.get("saved") == "settings":
            msg = "Settings saved."
            open_settings = True
        body = f"""
        <div class="cards">
          <div class="card"><span class="label">Agents</span>
            <div class="value"><a href="/agents">{len(agents)}</a></div></div>
          <div class="card"><span class="label">Allowlist</span>
            <div class="value"><a href="/allowlist">{len(allowed)}</a></div></div>
          <div class="card"><span class="label">Sessions</span>
            <div class="value"><a href="/sessions">{len(sessions)}</a></div></div>
          <div class="card"><span class="label">Schedules</span>
            <div class="value"><a href="/schedules">{len(schedules)}</a></div></div>
          <div class="card"><span class="label">Running</span>
            <div class="value"><a href="/status">{running}</a></div></div>
          <div class="card"><span class="label">Queued threads</span>
            <div class="value"><a href="/status">{queued}</a></div></div>
          <div class="card"><span class="label">Telegram-ready</span>
            <div class="value">{tg_ready}</div></div>
        </div>
        <p class="hint">Agents &amp; allowlist live under the gear · Settings.</p>
        """
        return page(
            "Dashboard",
            body,
            msg,
            active="dashboard",
            banner_class=banner_class,
            open_settings=open_settings,
        )

    @app.post("/settings/save")
    async def settings_save(
        streaming_enabled: str | None = Form(None),
        job_done_ping: str | None = Form(None),
        max_concurrent_jobs: str = Form("2"),
        discord_guild_id: str = Form(""),
        cursor_model: str = Form(""),
        cursor_sdk_model: str = Form(""),
        claude_model: str = Form(""),
        openrouter_model: str = Form(""),
        openrouter_online: str | None = Form(None),
        _: None = Depends(require_auth),
    ) -> RedirectResponse:
        jobs_raw = max_concurrent_jobs.strip() or "2"
        try:
            jobs = max(1, int(jobs_raw))
        except ValueError as exc:
            raise HTTPException(400, "max_concurrent_jobs must be an integer") from exc
        stream_on = streaming_enabled is not None
        db.set_setting("streaming_enabled", "true" if stream_on else "false")
        db.set_setting("job_done_ping", "true" if job_done_ping is not None else "false")
        db.set_setting("max_concurrent_jobs", str(jobs))
        guild = discord_guild_id.strip()
        if guild:
            if not guild.isdigit():
                raise HTTPException(400, "discord_guild_id must be numeric")
            db.set_setting("discord_guild_id", guild)
        else:
            db.set_setting("discord_guild_id", "")
        db.set_setting("backend.cursor-cli.model", cursor_model.strip())
        db.set_setting("backend.cursor-sdk.model", cursor_sdk_model.strip())
        db.set_setting("backend.claude-cli.model", claude_model.strip())
        db.set_setting("backend.openrouter.model", openrouter_model.strip())
        db.set_setting(
            "backend.openrouter.online",
            "true" if openrouter_online is not None else "false",
        )
        if gateway is not None:
            gateway.config.streaming_enabled = stream_on
        return RedirectResponse("/?saved=settings", status_code=303)

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
                f"<td>{html.escape(str(job.get('schedule_id') or '—'))}</td>"
                f"<td>{html.escape(preview)}</td>"
                f"<td>{job['queue_behind']}</td>"
                f"<td>{job['pid'] or '—'}</td>"
                "</tr>"
            )
            job_cards.append(
                f"""<div class="job-card">
                  <strong>{html.escape(agent)}</strong> · {job['elapsed_sec']}s
                  {" · cron `" + html.escape(str(job['schedule_id'])) + "`" if job.get("schedule_id") else ""}
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
            <tr><th>Session</th><th>Agent</th><th>Elapsed</th><th>Cron</th><th>Prompt</th><th>Queued</th><th>PID</th></tr>
            {"".join(run_rows) or "<tr><td colspan=7><em>none</em></td></tr>"}
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

    def _secret_names() -> list[str]:
        names = set(WELL_KNOWN_SECRETS)
        names.update(db.list_secret_names())
        for row in db.list_agent_rows():
            for key in ("discord_token_env", "telegram_token_env"):
                raw = str(row.get(key) or "").strip()
                if raw:
                    names.add(raw)
        return sorted(names)

    @app.get("/secrets", response_class=HTMLResponse)
    async def secrets_page(
        request: Request, _: None = Depends(require_auth)
    ) -> HTMLResponse:
        rows: list[str] = []
        for name in _secret_names():
            source = db.secret_source(name)
            if source == "admin":
                status_html = '<span class="badge on">set (admin)</span>'
            elif source == "env":
                status_html = '<span class="badge">set (env)</span>'
            else:
                status_html = '<span class="badge warn">missing</span>'
            clear_btn = ""
            if source == "admin":
                clear_btn = (
                    f"<form method='post' action='/secrets/delete' class='row-actions'>"
                    f"<input type='hidden' name='name' value='{html.escape(name)}'>"
                    f"<button type='submit' class='danger'>Clear</button></form>"
                )
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(name)}</code></td>"
                f"<td>{status_html}</td>"
                "<td>"
                f"<form method='post' action='/secrets/save' class='row-actions'>"
                f"<input type='hidden' name='name' value='{html.escape(name)}'>"
                f"<input type='password' name='value' autocomplete='new-password' "
                f"placeholder='new value' required>"
                f"<button type='submit'>Save</button></form>"
                f"{clear_btn}"
                "</td></tr>"
            )
        msg = ""
        if request.query_params.get("saved"):
            msg = "Secret saved. OpenRouter / admin password apply on the next request; Discord/Telegram tokens need /rebuild."
        if request.query_params.get("cleared"):
            msg = "Admin secret cleared (env fallback still used if set)."
        err = request.query_params.get("error")
        banner_class = "warn" if err else ""
        if err:
            msg = html.escape(err)
        body = f"""
        <p class="hint">Values are stored in SQLite (<code>gateway.db</code>, gitignored) and
        never shown here. Admin value wins over <code>.env</code>. Clear drops the admin
        copy only.</p>
        <div class="table-wrap">
          <table>
            <tr><th>Name</th><th>Status</th><th>Set / replace</th></tr>
            {"".join(rows)}
          </table>
        </div>
        <h3 class="section">Add another key</h3>
        <form method="post" action="/secrets/save">
          <label>Name
            <input type="text" name="name" required pattern="[A-Z][A-Z0-9_]*"
              placeholder="MY_API_KEY" autocomplete="off"></label>
          <label>Value
            <input type="password" name="value" required autocomplete="new-password"></label>
          <button type="submit">Save secret</button>
        </form>
        """
        return page("Secrets", body, msg, active="allowlist", banner_class=banner_class)

    @app.post("/secrets/save")
    async def secrets_save(
        name: str = Form(...),
        value: str = Form(...),
        _: None = Depends(require_auth),
    ) -> RedirectResponse:
        try:
            db.set_secret(name, value)
        except ValueError as exc:
            return RedirectResponse(
                f"/secrets?error={quote(str(exc), safe='')}", status_code=303
            )
        return RedirectResponse("/secrets?saved=1", status_code=303)

    @app.post("/secrets/delete")
    async def secrets_delete(
        name: str = Form(...), _: None = Depends(require_auth)
    ) -> RedirectResponse:
        db.delete_secret(name)
        return RedirectResponse("/secrets?cleared=1", status_code=303)

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
        <p class="hint">Profiles in SQLite. Put token <em>values</em> in
        <a href="/secrets">Secrets</a> (or <code>.env</code>); here you only name the key
        (<code>token_env</code>). Same <code>token_env</code> = <strong>one Discord bot</strong>;
        channel id + <code>/music</code> / <code>/general</code> pick the profile.
        <code>openrouter</code> needs <code>OPENROUTER_API_KEY</code>;
        <code>claude-cli</code> needs host <code>claude</code> login.</p>
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
          <label>Default backend (<code>cursor-cli</code>, <code>cursor-sdk</code>, <code>claude-cli</code>, <code>openrouter</code>)
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
            Optional — create bots via @BotFather, set <code>TELEGRAM_TOKEN_*</code> in
            <a href="/secrets">Secrets</a>, allowlist your Telegram user ID, enable here, then restart.
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

    def _unique_schedule_id(name: str) -> str:
        base = slug_id(name)
        sid = base
        n = 2
        while db.get_schedule(sid) is not None:
            sid = f"{base}-{n}"
            n += 1
        return sid

    def _agent_options(selected: str) -> str:
        parts = []
        for row in db.list_agent_rows():
            aid = str(row["id"])
            sel = " selected" if aid == selected else ""
            label = f"{row.get('display_name') or aid} ({aid})"
            parts.append(
                f'<option value="{html.escape(aid)}"{sel}>{html.escape(label)}</option>'
            )
        return "".join(parts)

    @app.get("/schedules", response_class=HTMLResponse)
    async def schedules_page(
        request: Request, _: None = Depends(require_auth)
    ) -> HTMLResponse:
        msg = ""
        banner = ""
        if request.query_params.get("saved"):
            msg = "Schedule saved."
        elif request.query_params.get("ran"):
            msg = f"Run requested: {html.escape(request.query_params.get('ran') or '')}."
        elif request.query_params.get("err"):
            msg = html.escape(request.query_params.get("err") or "Error")
            banner = "err"
        rows_html: list[str] = []
        running_keys = set()
        if gateway is not None:
            for job in gateway.live_status()["running"]:
                sid = job.get("schedule_id")
                if sid:
                    running_keys.add(str(sid))
        for row in db.list_schedules():
            sid = str(row["id"])
            status = str(row.get("last_status") or "—")
            if sid in running_keys:
                status = "running"
            nxt = (row.get("next_run_at") or "—")[:19].replace("T", " ")
            last = (row.get("last_run_at") or "—")[:19].replace("T", " ")
            url = row.get("last_thread_url") or ""
            thread_cell = (
                f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer">thread</a>'
                if url
                else "—"
            )
            en = bool(row.get("enabled"))
            toggle = "Disable" if en else "Enable"
            err = html.escape((row.get("last_error") or "")[:80])
            rows_html.append(
                "<tr>"
                f"<td><code>{html.escape(sid)}</code></td>"
                f"<td>{html.escape(str(row['name']))}</td>"
                f"<td>{html.escape(str(row['agent_id']))}</td>"
                f"<td><code>{html.escape(str(row['cron_expr']))}</code></td>"
                f"<td>{html.escape(str(row.get('timezone') or DEFAULT_TZ))}</td>"
                f"<td>{'on' if en else 'off'}</td>"
                f"<td>{html.escape(status)}</td>"
                f"<td>{html.escape(nxt)}</td>"
                f"<td>{html.escape(last)}</td>"
                f"<td>{thread_cell}</td>"
                f"<td class='row-actions'>"
                f"<form method='post' action='/schedules/{html.escape(sid)}/toggle'>"
                f"<button type='submit'>{toggle}</button></form>"
                f"<form method='post' action='/schedules/{html.escape(sid)}/run'>"
                f"<button type='submit'>Run now</button></form>"
                f"<a href='/schedules/{html.escape(sid)}/edit'>Edit</a>"
                f"<form method='post' action='/schedules/{html.escape(sid)}/delete' "
                f"onsubmit=\"return confirm('Delete {html.escape(sid)}?');\">"
                f"<button type='submit' class='danger'>Delete</button></form>"
                f"</td></tr>"
                + (f"<tr><td colspan=11 class='muted'>{err}</td></tr>" if err else "")
            )
        agents = db.list_agent_rows()
        agent_opts = _agent_options(str(agents[0]["id"]) if agents else "")
        body = f"""
        <p class="hint">Each run posts in the home channel and opens a <strong>public Discord thread</strong> in that agent’s
        home channel (no <code>--resume</code> from the previous run). 5-field cron in the
        schedule timezone (default <code>{html.escape(DEFAULT_TZ)}</code>).
        Status auto-refresh 10s. Discord <code>/schedule</code> lists these.</p>
        <div class="table-wrap">
          <table>
            <tr><th>Id</th><th>Name</th><th>Agent</th><th>Cron</th><th>TZ</th>
            <th>On</th><th>Status</th><th>Next</th><th>Last</th><th>Thread</th><th></th></tr>
            {"".join(rows_html) or "<tr><td colspan=11><em>none yet</em></td></tr>"}
          </table>
        </div>
        <h3 class="section">Add schedule</h3>
        <form method="post" action="/schedules/save">
          <label>Name
            <input type="text" name="name" required maxlength="{MAX_NAME}" placeholder="Morning health check"></label>
          <label>Agent
            <select name="agent_id">{agent_opts}</select></label>
          <label>Cron (min hour day month weekday)
            <input type="text" name="cron_expr" required placeholder="0 9 * * *"></label>
          <label>Timezone
            <input type="text" name="timezone" value="{html.escape(DEFAULT_TZ)}" placeholder="{html.escape(DEFAULT_TZ)}"></label>
          <label>Prompt
            <textarea name="prompt" required rows="5" maxlength="{MAX_PROMPT}"
              placeholder="What the agent should do each run"></textarea></label>
          <label><input type="checkbox" name="enabled" checked> Enabled</label>
          <p><button type="submit">Create</button></p>
        </form>
        """
        return page(
            "Schedules",
            body,
            msg,
            active="schedules",
            refresh=10,
            banner_class=banner,
        )

    @app.get("/schedules/{schedule_id}/edit", response_class=HTMLResponse)
    async def schedules_edit(
        schedule_id: str, _: None = Depends(require_auth)
    ) -> HTMLResponse:
        row = db.get_schedule(schedule_id)
        if row is None:
            raise HTTPException(404, "Unknown schedule")
        en = "checked" if row.get("enabled") else ""
        body = f"""
        <form method="post" action="/schedules/save">
          <input type="hidden" name="id" value="{html.escape(schedule_id)}">
          <label>Name
            <input type="text" name="name" required maxlength="{MAX_NAME}"
              value="{html.escape(str(row['name']))}"></label>
          <label>Agent
            <select name="agent_id">{_agent_options(str(row['agent_id']))}</select></label>
          <label>Cron
            <input type="text" name="cron_expr" required
              value="{html.escape(str(row['cron_expr']))}"></label>
          <label>Timezone
            <input type="text" name="timezone"
              value="{html.escape(str(row.get('timezone') or DEFAULT_TZ))}"></label>
          <label>Prompt
            <textarea name="prompt" required rows="8" maxlength="{MAX_PROMPT}">{html.escape(str(row['prompt']))}</textarea></label>
          <label><input type="checkbox" name="enabled" {en}> Enabled</label>
          <p><button type="submit">Save</button>
            <a href="/schedules">Cancel</a></p>
        </form>
        """
        return page(f"Edit {schedule_id}", body, active="schedules")

    @app.post("/schedules/save")
    async def schedules_save(
        name: str = Form(...),
        agent_id: str = Form(...),
        cron_expr: str = Form(...),
        timezone: str = Form(DEFAULT_TZ),
        prompt: str = Form(...),
        enabled: str | None = Form(None),
        id: str = Form(""),
        _: None = Depends(require_auth),
    ) -> RedirectResponse:
        name = name.strip()
        prompt = prompt.strip()
        agent_id = agent_id.strip()
        if not name or not prompt:
            raise HTTPException(400, "name and prompt required")
        if len(name) > MAX_NAME or len(prompt) > MAX_PROMPT:
            raise HTTPException(400, "name or prompt too long")
        if db.get_agent_row(agent_id) is None:
            raise HTTPException(400, "unknown agent")
        try:
            cron_expr = validate_cron(cron_expr)
            timezone = validate_timezone(timezone)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        sid = id.strip()
        if sid:
            if db.get_schedule(sid) is None:
                raise HTTPException(404, "Unknown schedule")
        else:
            sid = _unique_schedule_id(name)
        nxt = next_run_iso(cron_expr, timezone)
        db.upsert_schedule(
            {
                "id": sid,
                "name": name,
                "agent_id": agent_id,
                "cron_expr": cron_expr,
                "timezone": timezone,
                "prompt": prompt,
                "enabled": enabled is not None,
                "next_run_at": nxt,
            }
        )
        return RedirectResponse("/schedules?saved=1", status_code=303)

    @app.post("/schedules/{schedule_id}/toggle")
    async def schedules_toggle(
        schedule_id: str, _: None = Depends(require_auth)
    ) -> RedirectResponse:
        row = db.get_schedule(schedule_id)
        if row is None:
            raise HTTPException(404, "Unknown schedule")
        db.set_schedule_enabled(schedule_id, not bool(row.get("enabled")))
        return RedirectResponse("/schedules", status_code=303)

    @app.post("/schedules/{schedule_id}/run")
    async def schedules_run(
        schedule_id: str, _: None = Depends(require_auth)
    ) -> RedirectResponse:
        if gateway is None or gateway.scheduler is None:
            raise HTTPException(503, "scheduler not running")
        result = await gateway.scheduler.fire(schedule_id, force=True)
        return RedirectResponse(
            f"/schedules?ran={quote(result)}", status_code=303
        )

    @app.post("/schedules/{schedule_id}/delete")
    async def schedules_delete(
        schedule_id: str, _: None = Depends(require_auth)
    ) -> RedirectResponse:
        db.delete_schedule(schedule_id)
        return RedirectResponse("/schedules", status_code=303)

    @app.get("/sessions", response_class=HTMLResponse)
    async def sessions_page(_: None = Depends(require_auth)) -> HTMLResponse:
        data = db.list_sessions()
        rows = []
        for key, row in data.items():
            sid = row.get("session_id") or ""
            model = row.get("model") or ""
            backend = row.get("backend") or ""
            updated = (row.get("updated_at") or "")[:19].replace("T", " ")
            rows.append(
                f"<tr><td><code title=\"{html.escape(key)}\">{html.escape(_short_key(key, 40))}</code></td>"
                f"<td>{html.escape(row.get('title') or '')}</td>"
                f"<td><code>{html.escape(sid[:16])}{'…' if len(sid) > 16 else ''}</code></td>"
                f"<td>{html.escape(backend) or '—'}</td>"
                f"<td>{html.escape(model) or '—'}</td>"
                f"<td>{html.escape(updated) or '—'}</td>"
                f"<td><form method='post' action='/sessions/clear' class='row-actions'>"
                f"<input type='hidden' name='key' value='{html.escape(key)}'>"
                f"<button type='submit' class='danger'>Clear</button></form></td></tr>"
            )
        body = f"""
        <p class="hint">Thread ↔ backend session IDs. In chat, <code>/close</code> archives
        the Discord thread and keeps <code>--resume</code>. <code>/new</code> only drops resume.
        <strong>Clear</strong> here forgets the mapping (hard delete).</p>
        <form method="post" action="/sessions/prune" class="row-actions" style="margin-bottom:1rem">
          <button type="submit" class="danger">Prune empty (no resume id)</button>
        </form>
        <div class="table-wrap">
          <table>
            <tr><th>Key</th><th>Title</th><th>Session</th><th>Backend</th><th>Model</th><th>Updated</th><th></th></tr>
            {"".join(rows) or "<tr><td colspan=7><em>none</em></td></tr>"}
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

    @app.post("/sessions/prune")
    async def sessions_prune(_: None = Depends(require_auth)) -> RedirectResponse:
        db.prune_empty_sessions()
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
