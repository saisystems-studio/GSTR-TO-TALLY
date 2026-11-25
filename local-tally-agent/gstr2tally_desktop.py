git diff --cached --name-only

git diff --cached --check

git status --short"""GSTR2Tally Windows desktop shell: a dedicated WebView2 window around the existing GSTR2Tally web app.

It contains NO business logic and NO secrets (no Nexus product key / signing secret). The React app and Django backend stay the
single implementation; this shell only (1) makes sure the bundled local Tally Connector is running, (2) shows a loading / error
screen, and (3) opens the configured HTTPS GSTR2Tally URL in its own window."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import webview

from windows_runtime import single_instance

APP_NAME = 'GSTR2Tally'
HEALTH_URL = 'http://127.0.0.1:47831/health'
APP_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'GSTR2Tally'


def load_config() -> dict:
    """desktop.json (next to the EXE): {"web_url": "https://<host>/gstr2tally/"}. Falls back to release.json origin + /gstr2tally/.
    Only https URLs are accepted (plain http only for loopback development)."""
    cfg: dict = {}
    for name in ('desktop.json', 'release.json'):
        path = APP_DIR / name
        if path.exists():
            try:
                cfg.update(json.loads(path.read_text(encoding='utf-8-sig')))
            except (OSError, ValueError):
                pass
    url = str(cfg.get('web_url') or (str(cfg.get('origin', '')).rstrip('/') + '/gstr2tally/')).strip()
    parts = urlsplit(url)
    loopback = parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost')
    if (parts.scheme != 'https' and not loopback) or not parts.hostname or parts.username or parts.password:
        raise ValueError('desktop.json web_url must be an https URL.')
    return {'web_url': url}


def connector_running() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as response:
            return json.loads(response.read().decode()).get('app') == 'GSTR2TallyConnector'
    except (OSError, ValueError, urllib.error.URLError):
        return False


def ensure_connector() -> str:
    """Reuse a running Connector; otherwise start the bundled one (it enforces its own single instance and port)."""
    if connector_running():
        return 'running'
    exe = APP_DIR.parent / 'Connector' / 'GSTR2TallyConnector.exe'
    if not exe.exists():
        exe = APP_DIR / 'Connector' / 'GSTR2TallyConnector.exe'
    if not exe.exists():
        return 'missing'
    subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=0x08000000 | 0x00000008, close_fds=True)  # no window, detached
    for _ in range(20):
        time.sleep(0.5)
        if connector_running():
            return 'started'
    return 'not-responding'


def site_reachable(url: str) -> tuple[bool, str]:
    """TLS is verified (default context). Any HTTP answer below 500 means the site is up."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='GET'), timeout=10) as response:
            return True, f'HTTP {response.status}'
    except urllib.error.HTTPError as exc:
        return exc.code < 500, f'HTTP {exc.code}'
    except Exception as exc:  # noqa: BLE001 - shown to the user as a diagnostic
        return False, type(exc).__name__ + (f': {exc.reason}' if getattr(exc, 'reason', None) else '')


PAGE = '''<!doctype html><meta charset="utf-8"><title>GSTR2Tally</title>
<style>body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#f3f8fd;font-family:Segoe UI,sans-serif;color:#0d3177}}
.card{{text-align:center;max-width:520px;padding:32px}}h1{{margin:0 0 8px;font-size:26px}}p{{color:#46607f;line-height:1.5}}
.spin{{width:42px;height:42px;margin:18px auto;border:4px solid #cfe3f6;border-top-color:#0868d1;border-radius:50%;animation:s 1s linear infinite}}
@keyframes s{{to{{transform:rotate(360deg)}}}}button{{padding:10px 22px;border:0;border-radius:9px;background:#0d3177;color:#fff;font-weight:700;cursor:pointer}}
.bad{{color:#991b1b}}small{{color:#6b7f99}}</style><div class="card">{body}</div>'''


def screen(title: str, text: str, spinner: bool = False, retry: bool = False, detail: str = '') -> str:
    body = f'<h1>{title}</h1>' + ('<div class="spin"></div>' if spinner else '') + f'<p>{text}</p>'
    if detail:
        body += f'<p><small>{detail}</small></p>'
    if retry:
        body += '<button onclick="pywebview.api.retry()">Retry</button>'
    return PAGE.format(body=body)


class Api:
    def __init__(self):
        self.window = None
        self.url = ''

    def retry(self):
        threading.Thread(target=self.boot, daemon=True).start()

    def boot(self):
        w = self.window
        w.load_html(screen('Starting GSTR2Tally', 'Starting the local Tally Connector and connecting…', spinner=True))
        connector = ensure_connector()
        ok, detail = site_reachable(self.url)
        if not ok:
            w.load_html(screen('Cannot reach GSTR2Tally', 'Check your internet connection. Your data was not changed.', retry=True, detail=f'{self.url} — {detail}'))
            return
        if connector in ('missing', 'not-responding'):
            # The app still opens; the registration/Import screens show their own accurate "Connector Offline" state.
            pass
        w.load_url(self.url)


def main() -> int:
    try:
        config = load_config()
    except ValueError as exc:
        webview.create_window(APP_NAME, html=screen('Configuration error', str(exc)), width=640, height=420)
        webview.start()
        return 2
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with single_instance('Local\\GSTR2TallyDesktop') as acquired:
        if not acquired:
            return 0
        api = Api()
        api.url = config['web_url']
        api.window = webview.create_window(APP_NAME, html=screen('Starting GSTR2Tally', 'Please wait…', spinner=True), js_api=api,
                                           width=1366, height=820, min_size=(1024, 640), resizable=True, text_select=True)
        webview.start(api.boot, private_mode=False, storage_path=str(DATA_DIR / 'webview'), gui='edgechromium')
    return 0


if __name__ == '__main__':
    sys.exit(main())
