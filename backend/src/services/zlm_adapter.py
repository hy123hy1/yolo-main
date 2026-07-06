from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
import json


class ZLMAdapter:
    def __init__(
        self,
        play_base: str,
        api_base: str | None = None,
        webrtc_base: str | None = None,
        secret: str = "",
        public_rtsp_host: str | None = None,
        public_rtmp_host: str | None = None,
        public_http_host: str | None = None,
        rtsp_port: int = 554,
        rtmp_port: int = 1935,
        http_port: int = 8010,
        https_port: int = 443,
        hls_file: str = "hls.m3u8",
        httpflv_suffix: str = ".live.flv",
        webrtc_path: str = "/index/api/webrtc",
    ):
        self.play_base = play_base.rstrip("/")
        self.api_base = (api_base or play_base).rstrip("/")
        self.webrtc_base = (webrtc_base or play_base).rstrip("/")
        self.secret = secret
        self.public_rtsp_host = public_rtsp_host
        self.public_rtmp_host = public_rtmp_host
        self.public_http_host = public_http_host
        self.rtsp_port = rtsp_port
        self.rtmp_port = rtmp_port
        self.http_port = http_port
        self.https_port = https_port
        self.hls_file = hls_file
        self.httpflv_suffix = httpflv_suffix
        self.webrtc_path = webrtc_path if webrtc_path.startswith("/") else f"/{webrtc_path}"

    def build_play_urls(self, app: str, stream: str, node_id: str | None = None) -> dict[str, str]:
        play_parsed = urlparse(self.play_base)
        scheme = play_parsed.scheme or "http"
        hostname = self.public_http_host or play_parsed.hostname or "localhost"
        http_port = self.https_port if scheme == "https" else self.http_port
        http_host = f"{hostname}:{http_port}"
        http_base = f"{scheme}://{http_host}"
        rtsp_host = f"{self.public_rtsp_host or hostname}:{self.rtsp_port}"
        rtmp_host = f"{self.public_rtmp_host or hostname}:{self.rtmp_port}"

        webrtc_parsed = urlparse(self.webrtc_base)
        webrtc_scheme = webrtc_parsed.scheme or scheme
        webrtc_host = webrtc_parsed.netloc or f"{hostname}:{http_port}"
        if webrtc_scheme in {"http", "https"}:
            webrtc_scheme = "webrtc" if webrtc_scheme == "http" else "webrtcs"
        if not webrtc_host:
            webrtc_host = f"{hostname}:{http_port}"
        webrtc_query = urlencode({"app": app, "stream": stream, "type": "play"})
        browser_webrtc_query = urlencode({
            "app": app,
            "stream": stream,
            "type": "play",
            **({"node_id": node_id} if node_id else {}),
        })
        browser_suffix = f"?node_id={node_id}" if node_id else ""

        browser_hls = f"/proxy/sms/{app}/{stream}/{self.hls_file}{browser_suffix}"
        browser_httpflv = f"/proxy/sms/{app}/{stream}{self.httpflv_suffix}{browser_suffix}"
        browser_webrtc_api = f"/proxy/sms{self.webrtc_path}?{browser_webrtc_query}"

        return {
            "rtsp": f"rtsp://{rtsp_host}/{app}/{stream}",
            "rtmp": f"rtmp://{rtmp_host}/{app}/{stream}",
            "hls": f"{http_base}/{app}/{stream}/{self.hls_file}",
            "httpflv": f"{http_base}/{app}/{stream}{self.httpflv_suffix}",
            "webrtc": f"{webrtc_scheme}://{webrtc_host}{self.webrtc_path}?{webrtc_query}",
            "browser_hls": browser_hls,
            "browser_httpflv": browser_httpflv,
            "browser_webrtc_api": browser_webrtc_api,
            "browser_hls_url": f"{http_base}{browser_hls}",
            "browser_httpflv_url": f"{http_base}{browser_httpflv}",
            "browser_webrtc_api_url": f"{http_base}{browser_webrtc_api}",
        }

    def add_stream_proxy(self, app: str, stream: str, source_url: str) -> dict:
        payload = {
            "secret": self.secret,
            "vhost": "__defaultVhost__",
            "app": app,
            "stream": stream,
            "url": source_url,
            "enable_hls": 1,
            "enable_mp4": 0,
            "enable_rtsp": 1,
            "enable_rtmp": 1,
            "enable_ts": 1,
            "enable_fmp4": 1,
        }
        return self._post_json("/index/api/addStreamProxy", payload)

    def add_ffmpeg_source(
        self,
        source_url: str,
        target_url: str,
        timeout_ms: int = 10000,
        enable_hls: bool = True,
        enable_mp4: bool = False,
        ffmpeg_cmd_key: str = "ffmpeg.cmd",
    ) -> dict:
        payload = {
            "secret": self.secret,
            "src_url": source_url,
            "dst_url": target_url,
            "timeout_ms": timeout_ms,
            "enable_hls": 1 if enable_hls else 0,
            "enable_mp4": 1 if enable_mp4 else 0,
            "ffmpeg_cmd_key": ffmpeg_cmd_key,
        }
        return self._post_json("/index/api/addFFmpegSource", payload, timeout=30)

    def delete_ffmpeg_source(self, key: str) -> dict:
        payload = {
            "secret": self.secret,
            "key": key,
        }
        return self._post_json("/index/api/delFFmpegSource", payload, timeout=10)

    def delete_stream_proxy(self, app: str, stream: str) -> dict:
        payload = {
            "secret": self.secret,
            "vhost": "__defaultVhost__",
            "app": app,
            "stream": stream,
        }
        return self._post_json("/index/api/delStreamProxy", payload)

    def get_media_list(self, app: str, stream: str, schema: str = "rtsp") -> dict:
        query = urlencode({
            "secret": self.secret,
            "vhost": "__defaultVhost__",
            "schema": schema,
            "app": app,
            "stream": stream,
        })
        return self._get_json(f"/index/api/getMediaList?{query}")

    def get_server_config(self) -> dict:
        query = urlencode({"secret": self.secret})
        return self._get_json(f"/index/api/getServerConfig?{query}")

    def _post_json(self, path: str, payload: dict, timeout: int = 5) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.api_base}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw or "{}")

    def _get_json(self, path: str) -> dict:
        request = Request(
            f"{self.api_base}{path}",
            headers={"Content-Type": "application/json"},
            method="GET",
        )
        with urlopen(request, timeout=5) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw or "{}")
