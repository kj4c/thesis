# the http endpoint the iPhone / Meta glasses app talks to. both the headless
# server (serve.py) and the gui listener (gui/glasses_server.py) must honour the
# same contract, because the phone app doesn't know which one it's hitting.

import asyncio
import base64
import socket

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

import serve
from gui import glasses_server

JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 16
BIG_PHOTO = JPEG_HEADER + b"\x00" * (3 * 1024 * 1024)  # ~4 MB once base64'd


def _photo_payload(data=JPEG_HEADER, prompt="how much is this", filename="pic.jpg"):
    return {"prompt": prompt, "media_type": "image", "filename": filename,
            "media_data": base64.b64encode(data).decode()}


# ── serve.py ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def serve_client(monkeypatch, tmp_path):
    calls = {"run": [], "enrich": []}

    async def fake_run(workflow, prompt, config):
        calls["run"].append(prompt)
        if "explode" in prompt:
            raise RuntimeError("browser crashed")
        return f"answer to: {prompt[:40]}"

    async def fake_enrich(path, prompt):
        calls["enrich"].append(path)
        if "novision" in prompt:
            raise RuntimeError("vision down")
        return f"[photo context] {prompt}"

    monkeypatch.setattr(serve, "MEDIA_DIR", tmp_path)
    monkeypatch.setattr(serve, "run_turn_with_result", fake_run)
    monkeypatch.setattr(serve, "enrich_prompt_with_image", fake_enrich)

    app = serve.create_app(port=8765)
    app.on_startup.clear()
    app.on_cleanup.clear()
    app["workflow"], app["config"], app["lock"] = object(), {}, asyncio.Lock()

    client = TestClient(TestServer(app))
    await client.start_server()
    client.calls = calls
    client.media_dir = tmp_path
    yield client
    await client.close()


async def test_health(serve_client):
    r = await serve_client.get("/health")
    assert r.status == 200
    assert await r.json() == {"status": "ok", "port": 8765}


async def test_text_prompt_success_shape(serve_client):
    r = await serve_client.post("/data", json={"prompt": "open google"})
    body = await r.json()
    assert r.status == 200
    assert body["status"] == "success" and body["result"] == "answer to: open google"
    assert body["id"]


async def test_photo_is_saved_and_described_before_the_agent_runs(serve_client):
    r = await serve_client.post("/data", json=_photo_payload())
    assert r.status == 200
    saved = list(serve_client.media_dir.iterdir())
    assert len(saved) == 1 and saved[0].suffix == ".jpg"
    assert saved[0].read_bytes() == JPEG_HEADER
    assert serve_client.calls["run"] == ["[photo context] how much is this"]


async def test_vision_failure_falls_back_to_text_prompt(serve_client):
    r = await serve_client.post("/data", json=_photo_payload(prompt="novision price"))
    assert r.status == 200
    assert serve_client.calls["run"] == ["novision price"]


async def test_full_size_phone_photo_is_accepted(serve_client):
    # aiohttp rejects bodies over 1 MB by default; a jpeg from the glasses is bigger
    r = await serve_client.post("/data", json=_photo_payload(BIG_PHOTO))
    assert r.status == 200, await r.text()


async def test_agent_crash_returns_error_shape(serve_client):
    r = await serve_client.post("/data", json={"prompt": "explode"})
    body = await r.json()
    assert r.status == 500
    assert body["status"] == "error" and "browser crashed" in body["error"]


@pytest.mark.parametrize("payload,status", [
    ({}, 400), ({"prompt": "   "}, 400),
    ({"prompt": "x", "media_data": "%%% not base64 %%%"}, 400),
])
async def test_bad_requests_are_rejected(serve_client, payload, status):
    r = await serve_client.post("/data", json=payload)
    assert r.status == status
    assert "error" in await r.json()


async def test_invalid_json_is_rejected(serve_client):
    r = await serve_client.post("/data", data=b"not json", headers={"Content-Type": "application/json"})
    assert r.status == 400


async def test_filename_cannot_escape_media_dir(serve_client):
    r = await serve_client.post("/data", json=_photo_payload(filename="../../evil.sh"))
    assert r.status == 200
    saved = list(serve_client.media_dir.iterdir())
    assert len(saved) == 1 and saved[0].parent == serve_client.media_dir


async def test_requests_run_one_at_a_time(monkeypatch, serve_client):
    # one shared browser: two phone requests must not drive it at the same time
    active, peak = 0, 0

    async def slow_run(workflow, prompt, config):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "ok"

    monkeypatch.setattr(serve, "run_turn_with_result", slow_run)
    rs = await asyncio.gather(*(serve_client.post("/data", json={"prompt": f"p{i}"}) for i in range(3)))
    assert all(r.status == 200 for r in rs)
    assert peak == 1


# ── gui/glasses_server.py ────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def gui_server(monkeypatch, tmp_path):
    monkeypatch.setattr(glasses_server, "STORE_PATH", tmp_path / "store.json")
    monkeypatch.setattr(glasses_server, "MEDIA_DIR", tmp_path / "media")
    received = []

    def on_data(item, reply):
        # the gui answers from its own thread, later
        received.append(item)
        if "cancel" in item["prompt"]:
            reply(error="Cancelled on the computer.")
        else:
            reply(result=f"done: {item['prompt']}")

    server = glasses_server.GlassesServer(_free_port(), on_data)
    server.start()
    for _ in range(50):
        if server.running:
            break
        await asyncio.sleep(0.05)
    server.received = received
    server.url = f"http://127.0.0.1:{server.port}"
    yield server
    server.stop()


async def test_gui_server_health(gui_server):
    async with aiohttp.ClientSession() as s, s.get(gui_server.url + "/health") as r:
        assert (await r.json())["status"] == "ok"


async def test_gui_server_round_trip_with_photo(gui_server):
    async with aiohttp.ClientSession() as s, s.post(gui_server.url + "/data", json=_photo_payload(BIG_PHOTO)) as r:
        body = await r.json()
    assert r.status == 200
    assert body["status"] == "success" and body["result"] == "done: how much is this"
    item = gui_server.received[0]
    assert item["media_path"].endswith(".jpg")
    assert gui_server.store.get_pending()[0]["id"] == item["id"], "item persisted to the store"


async def test_gui_server_error_shape(gui_server):
    async with aiohttp.ClientSession() as s, s.post(gui_server.url + "/data", json={"prompt": "cancel it"}) as r:
        body = await r.json()
    assert r.status == 500 and body["status"] == "error" and body["error"]


async def test_gui_server_rejects_empty_prompt(gui_server):
    async with aiohttp.ClientSession() as s, s.post(gui_server.url + "/data", json={"prompt": ""}) as r:
        assert r.status == 400
