"""Offline behavior fixtures for the repaired image/MCP/HTTP callers; no service credentials."""

from __future__ import annotations

import contextlib
import http.client
import importlib
import importlib.metadata
import io
import subprocess
import sys
import unittest
import zlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server import Server


class BytesSocket:
    def __init__(self, data: bytes) -> None:
        self.stream = io.BytesIO(data)

    def makefile(self, mode: str) -> io.BytesIO:
        if mode != 'rb':
            raise ValueError('unexpected fixture socket mode')
        return self.stream


def urllib3_cases(name: str) -> None:
    """Exercise the real urllib3 reader with bounded in-memory HTTP bytes."""
    module = importlib.import_module(name)
    response_module = importlib.import_module(name + '.response')
    errors = importlib.import_module(name + '.exceptions')
    assert module.__version__ == '2.8.0'
    prefix = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n'
    oversized = prefix + b'\r\n' + b'1' * 65537 + b'\r\n'
    original = http.client.HTTPResponse(BytesSocket(oversized))
    original.begin()
    response = response_module.HTTPResponse(
        body=original, headers=original.headers, original_response=original, preload_content=False
    )
    try:
        list(response.read_chunked(amt=31))
    except errors.ProtocolError:
        assert response.closed
    else:
        raise AssertionError('overlong chunk size accepted')
    expected = b'fixture' * 65536
    compressed = zlib.compress(expected) + b'trailing bytes after deflate EOF'
    raw = (
        prefix
        + b'Content-Encoding: deflate\r\n\r\n'
        + f'{len(compressed):x}\r\n'.encode()
        + compressed
        + b'\r\n0\r\n\r\n'
    )
    original = http.client.HTTPResponse(BytesSocket(raw))
    original.begin()
    response = response_module.HTTPResponse(
        body=original, headers=original.headers, original_response=original, preload_content=False
    )
    try:
        assert b''.join(response.read_chunked(amt=31, decode_content=True)) == expected
    finally:
        response.close()


class RepairedHttp(unittest.TestCase):
    def test_application_and_actual_pip_vendor_reject_bad_chunk_and_terminate_deflate(self) -> None:
        for module in ('urllib3', 'pip._vendor.urllib3'):
            result = subprocess.run(
                [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--urllib3-case', module],
                capture_output=True,
                timeout=8,
                check=False,
            )
            self.assertLessEqual(len(result.stdout) + len(result.stderr), 65536)
            self.assertEqual(result.returncode, 0, 'bounded urllib3 security fixture failed')


class RepairedImages(unittest.TestCase):
    def test_real_image_codecs_and_corrupt_input(self) -> None:
        from PIL import Image, UnidentifiedImageError

        self.assertEqual(importlib.metadata.version('pillow'), '13.0.0')
        for format_name in ('PNG', 'JPEG', 'GIF', 'WEBP'):
            encoded = io.BytesIO()
            Image.new('RGB', (7, 5), (32, 64, 128)).save(encoded, format=format_name)
            with Image.open(io.BytesIO(encoded.getvalue())) as image:
                image.verify()
            with Image.open(io.BytesIO(encoded.getvalue())) as image:
                image.load()
                self.assertEqual(image.size, (7, 5))
        with self.assertRaises(UnidentifiedImageError):
            Image.open(io.BytesIO(b'not an image'))

    def test_exif_rotation_and_rgb_jpeg_normalization(self) -> None:
        from PIL import Image, ImageOps

        exif = Image.Exif()
        exif[274] = 6
        encoded = io.BytesIO()
        Image.new('RGB', (7, 5), (10, 20, 30)).save(encoded, format='JPEG', exif=exif)
        with Image.open(io.BytesIO(encoded.getvalue())) as image:
            normalized = ImageOps.exif_transpose(image).convert('RGB')
            self.assertEqual(normalized.size, (5, 7))
            self.assertNotIn(274, normalized.getexif())
            target = io.BytesIO()
            normalized.save(target, format='JPEG')
            with Image.open(io.BytesIO(target.getvalue())) as reopened:
                reopened.load()
                self.assertEqual(reopened.mode, 'RGB')


async def mcp_lifecycle() -> None:
    import anyio
    import mcp.types
    from mcp.server import Server
    from mcp.shared.memory import create_connected_server_and_client_session

    events: list[str] = []

    @contextlib.asynccontextmanager
    async def lifespan(server: Server[None]) -> AsyncIterator[None]:
        events.append('open')
        try:
            yield None
        finally:
            events.append('closed')

    server = Server('derivative-fixture', version='1.0', lifespan=lifespan)

    @server.list_tools()
    async def list_tools() -> list[mcp.types.Tool]:
        return []

    with anyio.fail_after(5):
        async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
            assert isinstance(await client.list_tools(), mcp.types.ListToolsResult)
            assert (await client.list_tools()).tools == []
    assert events == ['open', 'closed']


async def mcp_redirects() -> None:
    import anyio
    import httpx
    from mcp.shared._httpx_utils import request_within_origin

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == '/same':
            return httpx.Response(307, headers={'Location': '/done'})
        if request.url.path == '/cross':
            return httpx.Response(307, headers={'Location': 'https://other.invalid/stolen'})
        if request.url.path == '/port':
            return httpx.Response(307, headers={'Location': 'https://fixture.invalid:8443/stolen'})
        if request.url.path == '/rewrite':
            return httpx.Response(302, headers={'Location': '/done'})
        return httpx.Response(200, json={'ok': True})

    with anyio.fail_after(5):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            for path, expected_status, requests in [
                ('/same', 200, 2),
                ('/cross', 307, 1),
                ('/port', 307, 1),
                ('/rewrite', 302, 1),
            ]:
                seen.clear()
                result = await request_within_origin(
                    client,
                    'POST',
                    'https://fixture.invalid' + path,
                    headers={'Authorization': 'Bearer synthetic-fixture'},
                    json={},
                )
                assert result.status_code == expected_status and len(seen) == requests
                assert all(url.startswith('https://fixture.invalid/') for url in seen)
                assert result.is_closed


class RepairedMcp(unittest.TestCase):
    def test_real_session_handshake_tool_api_and_lifespan_cleanup(self) -> None:
        import anyio

        self.assertEqual(importlib.metadata.version('mcp'), '1.30.0')
        anyio.run(mcp_lifecycle)

    def test_transport_redirects_preserve_origin_and_method(self) -> None:
        import anyio

        anyio.run(mcp_redirects)

    def test_discovery_cannot_accept_different_issuer_or_path(self) -> None:
        from mcp.client.auth import OAuthFlowError
        from mcp.client.auth.utils import validate_metadata_issuer
        from mcp.shared.auth import OAuthMetadata

        metadata = OAuthMetadata.model_validate(
            {
                'issuer': 'https://issuer.invalid/',
                'authorization_endpoint': 'https://issuer.invalid/authorize',
                'token_endpoint': 'https://issuer.invalid/token',
                'response_types_supported': ['code'],
            }
        )
        validate_metadata_issuer(metadata, 'https://issuer.invalid')
        for expected in ('https://other.invalid', 'https://issuer.invalid/tenant'):
            with self.assertRaises(OAuthFlowError):
                validate_metadata_issuer(metadata, expected)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--urllib3-case':
        urllib3_cases(sys.argv[2])
    else:
        unittest.main()
