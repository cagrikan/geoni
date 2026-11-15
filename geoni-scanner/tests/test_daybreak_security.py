"""Daybreak bulgulari icin ag kullanmayan gerileme testleri."""
import asyncio
import gzip

import httpx
import pytest

import crawler
import db
import egress_proxy
from egress_proxy import pinned_egress_proxy, pinned_http_client
import ssrf_guard


def test_child_sitemap_private_host_rejected_before_request(monkeypatch):
    calls = []
    index = ("<sitemapindex><sitemap><loc>http://127.0.0.1/private</loc>"
             "</sitemap></sitemapindex>")

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=index)

    monkeypatch.setattr(ssrf_guard, "assert_public_host",
                        lambda host: None if host == "scan.example" else
                        ssrf_guard._is_public_ip(host) or
                        (_ for _ in ()).throw(ssrf_guard.BlockedHostError(host)))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await crawler.fetch_sitemap(client, "https://scan.example/")

    result = asyncio.run(run())
    assert result["urls"] == []
    assert calls == ["https://scan.example/sitemap.xml"]


def test_sitemap_body_budget_before_parse(monkeypatch):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"x" * (crawler.MAX_SITEMAP_BYTES + 1))

    monkeypatch.setattr(ssrf_guard, "assert_public_host", lambda host: None)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(ValueError, match="boyut sinirini asti"):
                await ssrf_guard.safe_get(client, "https://scan.example/sitemap.xml",
                                          max_response_bytes=crawler.MAX_SITEMAP_BYTES)
    asyncio.run(run())
    assert calls == ["https://scan.example/sitemap.xml"]


def test_compressed_sitemap_decodes_once(monkeypatch):
    xml = b"<urlset><url><loc>https://scan.example/ok</loc></url></urlset>"
    monkeypatch.setattr(ssrf_guard, "assert_public_host", lambda host: None)
    def respond(request):
        return httpx.Response(200, content=gzip.compress(xml),
                              headers={"Content-Encoding": "gzip"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await ssrf_guard.safe_get(client, "https://scan.example/sitemap.xml",
                                             max_response_bytes=crawler.MAX_SITEMAP_BYTES)
    response = asyncio.run(run())
    assert response.text.encode() == xml
    assert "content-encoding" not in response.headers


def test_sitemap_redirect_to_private_host_rejected_before_request(monkeypatch):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    original = ssrf_guard.assert_public_host
    monkeypatch.setattr(ssrf_guard, "assert_public_host",
                        lambda host: None if host == "scan.example" else original(host))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(ssrf_guard.BlockedHostError):
                await ssrf_guard.safe_get(client, "https://scan.example/sitemap.xml",
                                          max_response_bytes=crawler.MAX_SITEMAP_BYTES)
    asyncio.run(run())
    assert calls == ["https://scan.example/sitemap.xml"]


def test_browser_proxy_rejects_private_connect_before_upstream():
    async def run():
        async with pinned_egress_proxy() as proxy:
            address = httpx.URL(proxy)
            reader, writer = await asyncio.open_connection(address.host, address.port)
            writer.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n")
            await writer.drain()
            status = await asyncio.wait_for(reader.readline(), timeout=2)
            writer.close()
            await writer.wait_closed()
            return status
    assert asyncio.run(run()).startswith(b"HTTP/1.1 403")


def test_browser_proxy_connects_to_validated_ip_without_second_dns(monkeypatch):
    async def run():
        seen = []
        async def origin(reader, writer):
            seen.append(await reader.readexactly(4))
            writer.write(b"PONG")
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        original_open = asyncio.open_connection
        async def open_pinned(host, target_port, *args, **kwargs):
            if host == "203.0.113.7":
                assert target_port == 443
                return await original_open("127.0.0.1", port)
            return await original_open(host, target_port, *args, **kwargs)
        monkeypatch.setattr(egress_proxy, "assert_public_host",
                            lambda host: ["203.0.113.7"] if host == "scan.example" else [])
        monkeypatch.setattr(asyncio, "open_connection", open_pinned)
        try:
            async with pinned_egress_proxy() as proxy:
                address = httpx.URL(proxy)
                reader, writer = await original_open(address.host, address.port)
                writer.write(b"CONNECT scan.example:443 HTTP/1.1\r\n\r\n")
                await writer.drain()
                assert (await reader.readline()).startswith(b"HTTP/1.1 200")
                await reader.readline()
                writer.write(b"PING")
                await writer.drain()
                assert await reader.readexactly(4) == b"PONG"
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
        assert seen == [b"PING"]
    asyncio.run(run())


def test_browser_proxy_forwards_public_http_get_to_pinned_ip(monkeypatch):
    async def run():
        seen = []
        async def origin(reader, writer):
            seen.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        original_open = asyncio.open_connection
        async def open_pinned(host, target_port, *args, **kwargs):
            if host == "203.0.113.7":
                assert target_port == 80
                return await original_open("127.0.0.1", port)
            return await original_open(host, target_port, *args, **kwargs)
        monkeypatch.setattr(egress_proxy, "assert_public_host",
                            lambda host: ["203.0.113.7"] if host == "scan.example" else [])
        monkeypatch.setattr(asyncio, "open_connection", open_pinned)
        try:
            async with pinned_egress_proxy() as proxy:
                address = httpx.URL(proxy)
                reader, writer = await original_open(address.host, address.port)
                writer.write(b"GET http://scan.example/a?q=1 HTTP/1.1\r\nHost: scan.example\r\n\r\n")
                await writer.drain()
                assert b"OK" in await reader.read()
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
        assert seen[0].startswith(b"GET /a?q=1 HTTP/1.1\r\nHost: scan.example\r\n")
    asyncio.run(run())


def test_safe_get_uses_pinned_http_client_without_proxy_peer_false_alarm(monkeypatch):
    async def run():
        async def origin(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        original_open = asyncio.open_connection
        async def open_pinned(host, target_port, *args, **kwargs):
            if host == "203.0.113.7":
                return await original_open("127.0.0.1", port)
            return await original_open(host, target_port, *args, **kwargs)
        monkeypatch.setattr(egress_proxy, "assert_public_host", lambda host: ["203.0.113.7"])
        monkeypatch.setattr(ssrf_guard, "assert_public_host", lambda host: ["203.0.113.7"])
        monkeypatch.setattr(asyncio, "open_connection", open_pinned)
        try:
            async with pinned_http_client() as client:
                result = await ssrf_guard.safe_get(client, "http://scan.example/test")
                assert result.status_code == 200 and result.text == "OK"
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())


def test_private_audit_does_not_enter_public_leaderboard(monkeypatch):
    rows = [
        {"id": "private-job", "domain": "geoni.ai", "score": 99,
         "pages": "5", "private": "true", "internal": None, "created_at": "2026-11-14"},
        {"id": "public-job", "domain": "geoni.ai", "score": 88,
         "pages": "5", "private": None, "internal": None, "created_at": "2026-11-14"},
    ]
    class Response:
        status_code = 200
        def json(self): return rows
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            assert "private:result_json->>private" in url
            return Response()
    async def hidden(_): return set()
    monkeypatch.setattr(db, "SUPABASE_URL", "https://test.example")
    monkeypatch.setattr(db, "SUPABASE_SERVICE_KEY", "test-only")
    monkeypatch.setattr(db, "_get_leaderboard_hidden", hidden)
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda: Client())
    result = asyncio.run(db.get_ai_friendly_list())
    assert [row["job_id"] for row in result] == ["public-job"]
