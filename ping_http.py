#!/usr/bin/env python3
"""HTTP-сервер для README pixel и install ping."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from ping_tracker import (
    PIXEL_PNG,
    PING_TRACKER_VERSION,
    get_repo_inject_path,
    handle_ping_event,
    mark_repo_self_update,
    record_repo_inject_path,
)

log = logging.getLogger("ping_http")


async def handle_pixel(request: web.Request) -> web.Response:
    repo = (request.query.get("repo") or "").strip()
    inject = (request.query.get("inject") or "").strip()
    ip = request.remote or ""
    if request.headers.get("X-Forwarded-For"):
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    ua = request.headers.get("User-Agent", "")
    referer = request.headers.get("Referer", "")
    handle_ping_event(
        "view", repo, client_ip=ip, user_agent=ua, referer=referer,
        inject_path=inject or get_repo_inject_path(repo),
    )
    return web.Response(body=PIXEL_PNG, content_type="image/png")


async def handle_install(request: web.Request) -> web.Response:
    repo = (request.query.get("repo") or "").strip()
    git_user = (request.query.get("git") or "").strip()
    install_src = (request.query.get("msi") or request.query.get("src") or "").strip()
    inject = (request.query.get("inject") or "").strip()
    ip = request.remote or ""
    if request.headers.get("X-Forwarded-For"):
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    ua = request.headers.get("User-Agent", "")
    referer = request.headers.get("Referer", "")
    handle_ping_event(
        "install", repo, client_ip=ip, user_agent=ua, git_user=git_user,
        referer=referer, install_src=install_src,
        inject_path=inject or get_repo_inject_path(repo),
    )
    return web.Response(text="ok")


async def handle_health(_request: web.Request) -> web.Response:
    return web.Response(text=f"ok {PING_TRACKER_VERSION}")


async def handle_mark_self_update(request: web.Request) -> web.Response:
    secret = (os.getenv("PING_SECRET") or "").strip()
    if not secret:
        return web.Response(status=503, text="PING_SECRET not configured")

    body_secret = ""
    repo = (request.query.get("repo") or "").strip()
    inject_path = (request.query.get("inject_path") or "").strip()
    if request.can_read_body and request.content_type == "application/json":
        try:
            data = await request.json()
            body_secret = (data.get("secret") or "").strip()
            repo = repo or (data.get("repo") or "").strip()
            inject_path = inject_path or (data.get("inject_path") or "").strip()
        except Exception:
            pass
    if not body_secret:
        body_secret = (request.query.get("secret") or "").strip()

    if body_secret != secret:
        return web.Response(status=403, text="forbidden")
    if not repo or "/" not in repo:
        return web.Response(status=400, text="repo required")

    if inject_path:
        record_repo_inject_path(repo, inject_path)
    mark_repo_self_update(repo, remote=False, inject_path=inject_path)
    log.info("mark-self-update: %s inject=%s", repo, inject_path or "-")
    return web.Response(text="ok")


def create_ping_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/pixel", handle_pixel)
    app.router.add_get("/install", handle_install)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/mark-self-update", handle_mark_self_update)
    app.router.add_get("/mark-self-update", handle_mark_self_update)
    return app
