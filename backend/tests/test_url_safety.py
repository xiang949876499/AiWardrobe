import pytest
import anyio


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.0.0.8/",
        "http://192.168.1.20/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
        "http://user:pass@example.com/",
        "http://example.com:8080/",
    ],
)
def test_url_validation_rejects_private_or_non_web_destinations(url: str) -> None:
    from app.url_safety import UnsafeURL, validate_url_syntax

    with pytest.raises(UnsafeURL):
        validate_url_syntax(url)


def test_dns_resolution_rejects_a_hostname_with_any_private_address(monkeypatch) -> None:
    import socket

    from app.url_safety import UnsafeURL, resolve_public_url

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.2.3.4", 443)),
        ],
    )
    with pytest.raises(UnsafeURL):
        resolve_public_url("https://wardrobe.example")


def test_http_transport_uses_the_validated_dns_answer(monkeypatch) -> None:
    import app.safe_http as safe_http_module
    from app.safe_http import SafeHTTPTransport
    from app.url_safety import ResolvedURL

    class RecordingBackend:
        def __init__(self) -> None:
            self.hosts = []

        async def connect_tcp(self, host, port, **kwargs):
            self.hosts.append((host, port))
            return object()

        async def connect_unix_socket(self, *args, **kwargs):
            raise AssertionError("URL fetch must never open a Unix socket")

        async def sleep(self, seconds):
            return None

    monkeypatch.setattr(
        safe_http_module,
        "resolve_public_url",
        lambda url: ResolvedURL(url, "shop.example", 443, ("93.184.216.34",)),
    )
    transport = SafeHTTPTransport()
    recording = RecordingBackend()
    transport._network._backend = recording

    async def run():
        await transport._network.allow_url("https://shop.example/product")
        monkeypatch.setattr(
            safe_http_module,
            "resolve_public_url",
            lambda url: (_ for _ in ()).throw(AssertionError("must use pinned DNS result")),
        )
        await transport._network.connect_tcp("shop.example", 443)
        await transport.aclose()

    anyio.run(run, backend="asyncio")
    assert recording.hosts == [("93.184.216.34", 443)]


def test_redirect_to_private_address_is_rejected_before_second_request() -> None:
    import anyio

    from app.product_extraction import ProductExtractionError, _fetch_limited

    class RedirectResponse:
        status_code = 302
        headers = {"location": "http://127.0.0.1/metadata"}

    class StreamContext:
        async def __aenter__(self):
            return RedirectResponse()

        async def __aexit__(self, *args):
            return None

    class FakeClient:
        def __init__(self) -> None:
            self.requested = []

        def stream(self, method: str, url: str, headers: dict[str, str]):
            self.requested.append(url)
            return StreamContext()

    client = FakeClient()

    async def run():
        await _fetch_limited(
            client,
            "https://shop.example/product",
            accept="text/html",
            max_bytes=100,
            allowed_content_types={"text/html"},
        )

    with pytest.raises(ProductExtractionError) as error:
        anyio.run(run, backend="asyncio")
    assert error.value.code == "unsafe_url_destination"
    assert client.requested == ["https://shop.example/product"]
