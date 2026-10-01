from __future__ import annotations

import anyio
import httpcore
import httpx

from app.url_safety import ResolvedURL, UnsafeURL, _normalize_host, resolve_public_url


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self._backend = httpcore.AnyIOBackend()
        self._allowed: dict[tuple[str, int], tuple[str, ...]] = {}

    async def allow_url(self, url: str) -> ResolvedURL:
        resolved = await anyio.to_thread.run_sync(resolve_public_url, url)
        self._allowed[(resolved.host, resolved.port)] = resolved.addresses
        return resolved

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        host_text = host.decode("ascii") if isinstance(host, bytes) else str(host)
        key = (_normalize_host(host_text), int(port))
        addresses = self._allowed.get(key)
        if not addresses:
            raise UnsafeURL("url_dns_not_validated")
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("no validated public address")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise UnsafeURL("unix_socket_not_allowed")

    async def sleep(self, seconds):
        await self._backend.sleep(seconds)


class _AsyncResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream) -> None:
        self._stream = stream

    async def __aiter__(self):
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class SafeHTTPTransport(httpx.AsyncBaseTransport):
    """HTTPX transport that pins each connection to its validated DNS answer."""

    def __init__(self) -> None:
        self._network = _PinnedNetworkBackend()
        self._pool = httpcore.AsyncConnectionPool(
            max_connections=10,
            max_keepalive_connections=5,
            retries=0,
            network_backend=self._network,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self._network.allow_url(str(request.url))
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        core_response = await self._pool.handle_async_request(core_request)
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_AsyncResponseStream(core_response.stream),
            extensions=core_response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
