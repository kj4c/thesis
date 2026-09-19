# local http server that receives data from the meta glasses companion app
#
# POST /data  { prompt, media_type, media_data (base64), filename }
# GET  /health  returns {"status": "ok"} so the phone app can check connectivity

import asyncio
import base64
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from aiohttp import web

BASE_DIR = Path(__file__).parent.parent
STORE_PATH = BASE_DIR / 'glasses_store.json'
MEDIA_DIR = BASE_DIR / 'glasses_media'


# persistent store for glasses data items

class GlassesStore:

    def __init__(self):
        MEDIA_DIR.mkdir(exist_ok=True)
        self._load()

    def _load(self):
        if STORE_PATH.exists():
            with open(STORE_PATH) as f:
                self._data = json.load(f)
        else:
            self._data = {'items': []}

    def _save(self):
        with open(STORE_PATH, 'w') as f:
            json.dump(self._data, f, indent=2)

    def add(self, prompt: str, media_type: str, media_path: str) -> dict:
        item = {
            'id': str(uuid.uuid4()),
            'prompt': prompt,
            'media_type': media_type,
            'media_path': media_path,
            'received_at': datetime.now().isoformat(),
            'status': 'pending',  # pending | acted | dismissed | cancelled
        }
        self._data['items'].append(item)
        self._save()
        return item

    def update_status(self, item_id: str, status: str):
        for item in self._data['items']:
            if item['id'] == item_id:
                item['status'] = status
                self._save()
                return

    def delete_media(self, item: dict):
        path = item.get('media_path', '')
        if path and os.path.exists(path):
            os.remove(path)

    def get_pending(self) -> list[dict]:
        return [i for i in self._data['items'] if i['status'] == 'pending']

    def get_dismissed(self) -> list[dict]:
        return [i for i in self._data['items'] if i['status'] == 'dismissed']


# http server

class GlassesServer:
    def __init__(self, port: int, on_data_received):
        self.port = port
        self._on_data = on_data_received
        self.store = GlassesStore()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        # returns false if already running
        if self._running:
            return False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)

    def restart(self, new_port: int):
        self.stop()
        # wait for old loop to finish before starting on new port
        if self._thread:
            self._thread.join(timeout=2)
        self.port = new_port
        self.start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        app = web.Application()
        app.router.add_get('/health', self._handle_health)
        app.router.add_post('/data', self._handle_data)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '0.0.0.0', self.port)
        await site.start()
        self._running = True

        while self._running:
            await asyncio.sleep(0.5)

    async def _shutdown(self):
        self._running = False
        if self._runner:
            await self._runner.cleanup()

    # request handlers

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.json_response({'status': 'ok', 'port': self.port})

    async def _handle_data(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid json'}, status=400)

        prompt = payload.get('prompt', '').strip()
        if not prompt:
            return web.json_response({'error': 'prompt is required'}, status=400)

        media_type = payload.get('media_type', 'unknown')
        media_data_b64 = payload.get('media_data', '')
        filename = payload.get('filename') or f'{uuid.uuid4()}.bin'

        # decode and save media to disk
        media_path = ''
        if media_data_b64:
            try:
                media_bytes = base64.b64decode(media_data_b64)
                media_path = str(MEDIA_DIR / filename)
                with open(media_path, 'wb') as f:
                    f.write(media_bytes)
            except Exception as exc:
                return web.json_response(
                    {'error': f'media decode failed: {exc}'}, status=400
                )

        item = self.store.add(prompt, media_type, media_path)
        self._on_data(item)

        return web.json_response({'status': 'received', 'id': item['id']})
