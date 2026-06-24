from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from crop_search_framework.dev_tools.http_client import CachedResponse, HttpClient, HttpError


class FakeResp:
    def __init__(self, status_code=200, json_payload=None, content=None, text=None, url="http://x", headers=None):
        self.status_code = status_code
        self._json = json_payload
        self.content = content if content is not None else (text or "").encode("utf-8")
        self.text = text if text is not None else ""
        self.url = url
        self.headers = headers or {}

    def json(self):
        return self._json


class BackoffTests(unittest.TestCase):
    def test_retries_429_then_succeeds(self):
        seq = [FakeResp(status_code=429), FakeResp(status_code=200, json_payload={"ok": 1})]
        sleeps = []
        client = HttpClient(getter=lambda *a, **k: seq.pop(0), sleeper=sleeps.append)
        resp = client.get_json("http://api/x")
        self.assertEqual(resp.json(), {"ok": 1})
        self.assertEqual(len(sleeps), 1)  # one backoff before the retry

    def test_exhausts_and_raises(self):
        client = HttpClient(getter=lambda *a, **k: FakeResp(status_code=503), max_retries=3, sleeper=lambda s: None)
        with self.assertRaises(HttpError):
            client.get_json("http://api/x")

    def test_4xx_is_not_retried(self):
        calls = {"n": 0}

        def getter(*a, **k):
            calls["n"] += 1
            return FakeResp(status_code=404)

        client = HttpClient(getter=getter, sleeper=lambda s: None)
        with self.assertRaises(HttpError):
            client.get_json("http://api/x")
        self.assertEqual(calls["n"], 1)


class CacheTests(unittest.TestCase):
    def test_json_cache_hit_avoids_second_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = {"n": 0}

            def getter(*a, **k):
                calls["n"] += 1
                return FakeResp(status_code=200, json_payload={"v": calls["n"]})

            client = HttpClient(cache_dir=Path(tmp), getter=getter)
            first = client.get_json("http://api/x", params={"q": "wheat"})
            second = client.get_json("http://api/x", params={"q": "wheat"})
            self.assertEqual(first.json(), {"v": 1})
            self.assertEqual(second.json(), {"v": 1})  # served from cache
            self.assertEqual(calls["n"], 1)

    def test_binary_cache_round_trips_text_headers_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp = FakeResp(
                status_code=200,
                content=b"<html>wheat</html>",
                url="http://site/final",
                headers={"Content-Type": "text/html"},
            )
            client = HttpClient(cache_dir=Path(tmp), getter=lambda *a, **k: resp)
            client.get_binary("http://site/x")
            # Second call hits cache; the getter would raise if invoked.
            client.getter = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used"))
            cached = client.get_binary("http://site/x")
            self.assertEqual(cached.content, b"<html>wheat</html>")
            self.assertEqual(cached.text, "<html>wheat</html>")
            self.assertEqual(cached.url, "http://site/final")
            self.assertEqual(cached.headers.get("Content-Type"), "text/html")


class CachedResponseTests(unittest.TestCase):
    def test_text_from_content(self):
        r = CachedResponse(status_code=200, url="u", content=b"abc")
        self.assertEqual(r.text, "abc")
        self.assertEqual(r.content, b"abc")


if __name__ == "__main__":
    unittest.main()
