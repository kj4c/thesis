# local http host so the Meta glasses can push prompts at the agent.
#
#   GET  /health -> {"status": "ok", "port": <port>}
#   POST /data   -> {"prompt": str,               (required)
#                    "media_type": str,           (optional, default "unknown")
#                    "media_data": base64 str,    (optional)
#                    "filename": str}             (optional, for the extension)
#            reply  {"status": "success"|"error", "id": <uuid>,
#                    "result": str,               (agent reply, on success)
#                    "error": str}                (on failure)
#
# run with:  python serve.py            (defaults to 0.0.0.0:8765)
#            GLASSES_PORT=9000 python serve.py
#
# ── how the phone/glasses app connects ───────────────────────────────────────
# requirements:
#   1. phone and this mac on the SAME wifi/lan (a private ip like 192.168.x.x
#      is not reachable from the public internet).
#   2. serve.py running here.
#   3. macos firewall must ALLOW incoming connections for python (click "allow"
#      on the first-run popup, or System Settings -> Network -> Firewall).
#
# then point the app at this mac, either:
#   - by mdns/bonjour: service "_http._tcp.local." named "Glasses Agent"
#     (survives ip changes; only if the app does discovery), or
#   - by ip: http://<this-mac-lan-ip>:8765   (find it: ipconfig getifaddr en0)
#
# sanity check from the phone's browser first: http://<ip>:8765/health
# should return {"status": "ok", ...}. if that works, the app will too.

import asyncio
import base64
import os
import socket
import time
import uuid
from pathlib import Path

from aiohttp import web
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

import llm
from glasses_vision import enrich_prompt_with_image
from graph import build_agent
from mvp import run_turn_with_result
from state import _start_resources, _run_config

DEFAULT_PORT = 8765
MEDIA_DIR = Path(__file__).parent / "glasses_media"
THREAD_ID = "glasses"
SERVICE_TYPE = "_http._tcp.local."
SERVICE_NAME = "Glasses Agent"


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def _register_mdns(port: int) -> AsyncZeroconf:
    # advertise the server over mdns so the phone app can find it by name
    # (e.g. "Glasses Agent._http._tcp.local.") without a hardcoded ip.
    ip = _lan_ip()
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{SERVICE_NAME}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"path": "/data", "health": "/health"},
        server=f"{socket.gethostname()}.local.",
    )
    azc = AsyncZeroconf()
    await azc.async_register_service(info)
    azc.info = info
    print(f"[glasses] mdns: discoverable as '{SERVICE_NAME}' at {ip}:{port}")
    return azc


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "port": request.app["port"]})


async def handle_data(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "prompt is required"}, status=400)

    item_id = str(uuid.uuid4())
    media_path: Path | None = None

    media_data = body.get("media_data")
    if media_data:
        media_type = body.get("media_type", "unknown")
        ext = os.path.splitext(body.get("filename", ""))[1] or ".bin"
        media_path = MEDIA_DIR / f"{item_id}{ext}"
        try:
            media_path.write_bytes(base64.b64decode(media_data))
            print(f"[glasses] saved {media_type} -> {media_path}")
        except Exception as exc:
            return web.json_response(
                {"error": f"media decode failed: {exc}"}, status=400
            )

    agent_prompt = prompt
    if media_path is not None:
        try:
            print("[glasses] analysing POV photo…")
            agent_prompt = await enrich_prompt_with_image(media_path, prompt)
            print(f"[glasses] vision context:\n{agent_prompt}\n")
        except Exception as exc:
            print(f"[glasses] vision failed ({exc!r}), using text prompt only")

    workflow = request.app["workflow"]
    config = request.app["config"]
    lock: asyncio.Lock = request.app["lock"]

    print(f"[glasses] running: {prompt!r}")
    start = time.perf_counter()
    async with lock:
        try:
            result = await run_turn_with_result(workflow, agent_prompt, config)
        except Exception as exc:
            print(f"[glasses] task failed: {exc!r}")
            return web.json_response(
                {"status": "error", "id": item_id, "error": str(exc)},
                status=500,
            )

    elapsed = time.perf_counter() - start
    print(f"[glasses] done in {elapsed:.1f}s")
    print(f"[glasses] reply: {result[:200]}{'…' if len(result) > 200 else ''}")
    return web.json_response(
        {"status": "success", "id": item_id, "result": result}
    )


async def start_background(app: web.Application):
    MEDIA_DIR.mkdir(exist_ok=True)
    session, tools, file_system = await _start_resources()
    app["session"] = session
    app["workflow"] = build_agent()
    app["config"] = _run_config(session, tools, file_system, THREAD_ID)
    app["lock"] = asyncio.Lock()
    app["mdns"] = await _register_mdns(app["port"])
    print(f"planner: {llm.PLANNER_PROVIDER} / "
          f"{llm.PLANNER_MODELS.get(llm.PLANNER_PROVIDER, '?')}")
    print(f"glasses vision: {llm.GLASSES_VISION_PROVIDER} / {llm.GLASSES_VISION_MODEL}")


async def cleanup_background(app: web.Application):
    azc = app.get("mdns")
    if azc is not None:
        await azc.async_unregister_service(azc.info)
        await azc.async_close()
    print("closing browser…")
    await app["session"].kill()


def main():
    port = int(os.environ.get("GLASSES_PORT", DEFAULT_PORT))
    app = web.Application()
    app["port"] = port
    app.router.add_get("/health", handle_health)
    app.router.add_post("/data", handle_data)
    app.on_startup.append(start_background)
    app.on_cleanup.append(cleanup_background)
    # 0.0.0.0 so the glasses (on the same lan) can reach it, not just localhost
    print(f"[glasses] listening on http://0.0.0.0:{port}  "
          f"(/health, POST /data)")
    web.run_app(app, host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()
