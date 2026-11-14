"""Chromium icin baglanti oncesi public-IP dogrulayan yerel CONNECT gecidi."""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx

from ssrf_guard import BlockedHostError, assert_public_host


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _handle(reader, writer):
    upstream = None
    try:
        first = await asyncio.wait_for(reader.readline(), timeout=10)
        if len(first) > 4096:
            raise ValueError("request line too large")
        parts = first.decode("ascii", "strict").split()
        if len(parts) != 3 or parts[2] != "HTTP/1.1":
            raise ValueError("invalid request")
        method, target, _ = parts
        if method == "CONNECT":
            if target.startswith("["):
                host, separator, port_text = target[1:].partition("]:")
            else:
                host, separator, port_text = target.rpartition(":")
            if not separator or not host or not port_text.isdecimal():
                raise ValueError("invalid target")
            port = int(port_text)
            http_path = None
        elif method in ("GET", "HEAD"):
            parsed = urlsplit(target)
            if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("invalid HTTP target")
            host = parsed.hostname
            port = parsed.port or 80
            http_path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        else:
            raise BlockedHostError("HTTP metodu izinli degil")
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
        # Header boyutunu sinirla; proxy kimlik bilgilerini origin'e aktarma.
        headers_size = 0
        headers = []
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            headers_size += len(line)
            if headers_size > 16384 or not line:
                raise ValueError("invalid headers")
            if line in (b"\r\n", b"\n"):
                break
            if b":" not in line:
                raise ValueError("invalid header")
            name = line.split(b":", 1)[0].strip().lower()
            if name in (b"content-length", b"transfer-encoding"):
                raise BlockedHostError("govdeli HTTP istegi izinli degil")
            if name not in (b"host", b"connection", b"proxy-connection", b"proxy-authorization"):
                headers.append(line)
        # Adi bir kez cozer ve o public IP'ye dogrudan baglanir: Chromium'un
        # ikinci DNS cozumunun private adrese kayma sansi kalmaz.
        addresses = await asyncio.to_thread(assert_public_host, host)
        if not addresses:
            raise BlockedHostError("host cozumlenemedi")
        last_error = None
        for ip in addresses:
            try:
                upstream = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=10)
                break
            except (OSError, TimeoutError) as exc:
                last_error = exc
        if upstream is None:
            raise last_error or ConnectionError("baglanti kurulamadi")
        upstream_reader, upstream_writer = upstream
        if http_path is None:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))
        else:
            request_line = f"{method} {http_path} HTTP/1.1\r\n".encode("ascii")
            host_header = f"Host: {host}{':' + str(port) if port != 80 else ''}\r\n".encode("ascii")
            upstream_writer.write(request_line + host_header + b"".join(headers)
                                  + b"Connection: close\r\n\r\n")
            await upstream_writer.drain()
            await _pipe(upstream_reader, writer)
    except (BlockedHostError, ValueError, UnicodeError, OSError, TimeoutError):
        if not writer.is_closing():
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            try:
                await writer.drain()
            except ConnectionError:
                pass
    finally:
        if upstream is not None:
            upstream[1].close()
        writer.close()


@asynccontextmanager
async def pinned_egress_proxy():
    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    finally:
        server.close()
        await server.wait_closed()


@asynccontextmanager
async def pinned_http_client(**kwargs):
    """safe_get cagiranlari icin DNS cozumunu baglantiya sabitle."""
    async with pinned_egress_proxy() as proxy_url:
        async with httpx.AsyncClient(proxy=proxy_url, **kwargs) as client:
            # httpx network_stream artik 127.0.0.1 proxy'sini gorur; asil
            # hedefin IP'sini proxy baglanti oncesinde dogruladi.
            client._geoni_pinned_egress = True
            yield client
