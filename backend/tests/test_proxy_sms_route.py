import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.routes import proxy_sms as proxy_sms_module


class _FakeQueryParams:
    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def get(self, key: str, default=None):
        return self._values.get(key, default)

    def multi_items(self):
        return list(self._values.items())


class _FakeRequest:
    def __init__(self, path_query: dict[str, str] | None = None):
        self.query_params = _FakeQueryParams(path_query)
        self.method = "GET"
        self.headers = {}

    async def body(self):
        return b""


class _FakeHeaders:
    def __init__(self, values: dict[str, str] | None = None, content_type: str = "video/x-flv"):
        self._values = dict(values or {})
        self._content_type = content_type

    def get_content_type(self):
        return self._content_type

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class _FakeUpstream:
    def __init__(self, payload: bytes):
        self.status = 200
        self.headers = _FakeHeaders()
        self._payload = payload
        self._offset = 0
        self.read_thread_ids: list[int] = []
        self.closed = False

    def read(self, size: int = -1):
        self.read_thread_ids.append(threading.get_ident())
        if self._offset >= len(self._payload):
            return b""
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class ProxySmsRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_sms_offloads_upstream_open_and_read_for_streaming(self):
        main_thread_id = threading.get_ident()
        open_thread_ids: list[int] = []
        fake_upstream = _FakeUpstream(b"stream-payload")

        def _fake_urlopen(*args, **kwargs):
            open_thread_ids.append(threading.get_ident())
            return fake_upstream

        request = _FakeRequest()

        with (
            patch.object(proxy_sms_module, "_resolve_upstream_base", AsyncMock(return_value="http://zlm-server:8010")),
            patch.object(proxy_sms_module, "urlopen", side_effect=_fake_urlopen),
        ):
            response = await proxy_sms_module.proxy_sms("rtp/test.live.flv", request)
            received = bytearray()
            async for chunk in response.body_iterator:
                received.extend(chunk)

        self.assertEqual(bytes(received), b"stream-payload")
        self.assertTrue(fake_upstream.closed)
        self.assertTrue(open_thread_ids)
        self.assertNotIn(main_thread_id, open_thread_ids)
        self.assertTrue(fake_upstream.read_thread_ids)
        self.assertNotIn(main_thread_id, fake_upstream.read_thread_ids)

