import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx


class ProductExtractionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProductMetadata:
    image_url: str
    title: str
    domain: str
    price: str | None = None


@dataclass(frozen=True)
class ExtractedProductImage:
    product_url: str
    source_image_url: str
    image_bytes: bytes
    content_type: str
    title: str
    domain: str
    price: str | None = None


@dataclass(frozen=True)
class _ImageCandidate:
    url: str
    score: int


class _ProductHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: list[dict[str, str]] = []
        self.images: list[dict[str, str]] = []
        self.scripts: list[str] = []
        self.json_ld: list[str] = []
        self.title = ""
        self._in_title = False
        self._in_script = False
        self._in_json_ld = False
        self._script_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "meta":
            self.meta.append(values)
        elif tag == "img":
            self.images.append(values)
        elif tag == "title":
            self._in_title = True
        elif tag == "script":
            self._in_script = True
            self._in_json_ld = values.get("type", "").lower() == "application/ld+json"
            self._script_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_script:
            self._in_script = False
            text = "".join(self._script_buffer).strip()
            if text:
                self.scripts.append(text)
                if self._in_json_ld:
                    self.json_ld.append(text)
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()
        elif self._in_script:
            self._script_buffer.append(data)


def select_product_metadata(html: str, product_url: str) -> ProductMetadata:
    parsed_url = urlparse(product_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ProductExtractionError("invalid_url", "Product URL must be an absolute http(s) URL")

    parser = _ProductHTMLParser()
    parser.feed(html)
    if _is_blocked_or_homepage(parser.title, parsed_url, html):
        raise ProductExtractionError("product_page_blocked", "Product page was blocked or redirected to a marketplace homepage")

    candidates: list[_ImageCandidate] = []
    price = _meta_price(parser.meta) or _json_ld_price(parser.json_ld) or _script_price(parser.scripts)

    for attrs in parser.meta:
        name = attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or ""
        content = attrs.get("content", "").strip()
        if not content:
            continue
        lowered = name.lower()
        if lowered == "og:image":
            candidates.append(_ImageCandidate(content, 100))
        elif lowered in {"twitter:image", "twitter:image:src"}:
            candidates.append(_ImageCandidate(content, 90))
        elif lowered in {"image", "product:image", "thumbnail"}:
            candidates.append(_ImageCandidate(content, 80))

    for image_url in _json_ld_images(parser.json_ld):
        candidates.append(_ImageCandidate(image_url, 75))

    for image_url in _script_images(parser.scripts):
        candidates.append(_ImageCandidate(image_url, 70))

    for attrs in parser.images:
        src = (attrs.get("src") or attrs.get("data-src") or attrs.get("data-original") or "").strip()
        if not src:
            continue
        score = 30
        src_lower = src.lower()
        if any(token in src_lower for token in ["product", "goods", "item", "sku", "hero"]):
            score += 20
        width = _int_attr(attrs.get("width"))
        height = _int_attr(attrs.get("height"))
        if width >= 500 or height >= 500:
            score += 20
        candidates.append(_ImageCandidate(src, score))

    if not candidates:
        raise ProductExtractionError("product_image_not_found", "No product image was found on the page")

    best = max(candidates, key=lambda candidate: candidate.score)
    return ProductMetadata(
        image_url=urljoin(product_url, best.url),
        title=parser.title.strip(),
        domain=parsed_url.netloc.lower(),
        price=price,
    )


async def extract_product_image(url: str) -> ExtractedProductImage:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProductExtractionError("invalid_url", "Product URL must be an absolute http(s) URL")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            page = await client.get(url)
            page.raise_for_status()
            metadata = select_product_metadata(page.text, str(page.url))
            image = await client.get(metadata.image_url, headers={"Accept": "image/*"})
            image.raise_for_status()
    except ProductExtractionError:
        raise
    except httpx.HTTPError as exc:
        raise ProductExtractionError("page_fetch_failed", "Product page could not be fetched") from exc

    content_type = image.headers.get("content-type", "image/jpeg").split(";")[0].strip() or "image/jpeg"
    if not content_type.startswith("image/"):
        raise ProductExtractionError("image_download_failed", "Selected product image is not an image")
    return ExtractedProductImage(
        product_url=url,
        source_image_url=metadata.image_url,
        image_bytes=image.content,
        content_type=content_type,
        title=metadata.title,
        domain=metadata.domain,
        price=metadata.price,
    )


def _is_blocked_or_homepage(title: str, parsed_url, html: str) -> bool:
    host = parsed_url.netloc.lower()
    path = parsed_url.path.strip("/")
    normalized_title = title.strip()
    if host in {"www.jd.com", "jd.com"} and not path:
        return True
    if host.endswith("jd.com") and "京东(JD.COM)-正品低价" in normalized_title and "item.jd.com" not in html:
        return True
    if "trade.m.jd.com" in host and "common/limit" in path:
        return True
    return False


def _meta_price(meta: list[dict[str, str]]) -> str | None:
    for attrs in meta:
        name = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        content = attrs.get("content", "").strip()
        if content and name in {"product:price:amount", "og:price:amount", "price"}:
            return _clean_price(content)
    return None


def _json_ld_price(json_ld_blocks: list[str]) -> str | None:
    for block in json_ld_blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        price = _collect_json_ld_price(parsed)
        if price:
            return price
    return None


def _collect_json_ld_price(value: object) -> str | None:
    if isinstance(value, list):
        for item in value:
            price = _collect_json_ld_price(item)
            if price:
                return price
    elif isinstance(value, dict):
        direct = value.get("price") or value.get("lowPrice") or value.get("highPrice")
        if direct:
            return _clean_price(str(direct))
        offers = value.get("offers")
        if isinstance(offers, (dict, list)):
            price = _collect_json_ld_price(offers)
            if price:
                return price
        for item in value.values():
            if isinstance(item, (dict, list)):
                price = _collect_json_ld_price(item)
                if price:
                    return price
    return None


def _script_price(scripts: list[str]) -> str | None:
    patterns = [
        r"""["'](?:jdPrice|p|price|salePrice|skuPrice)["']\s*:\s*["']?([0-9]+(?:\.[0-9]+)?)""",
        r"""(?:jdPrice|salePrice|skuPrice)\s*[:=]\s*["']([0-9]+(?:\.[0-9]+)?)["']""",
    ]
    for script in scripts:
        for pattern in patterns:
            match = re.search(pattern, script)
            if match:
                return _clean_price(match.group(1))
    return None


def _script_images(scripts: list[str]) -> list[str]:
    images: list[str] = []
    patterns = [
        r"""imageList\s*:\s*\[([^\]]+)\]""",
        r"""["']imageList["']\s*:\s*\[([^\]]+)\]""",
    ]
    for script in scripts:
        for pattern in patterns:
            for block in re.findall(pattern, script, flags=re.DOTALL):
                for value in re.findall(r"""["']([^"']+\.(?:jpg|jpeg|png|webp)(?:![^"']*)?)["']""", block, flags=re.I):
                    images.append(_normalize_script_image_url(value))
        for value in re.findall(
            r"""(?:imagePath|imageUrl|mainImage|skuImage)\s*[:=]\s*["']([^"']+\.(?:jpg|jpeg|png|webp)(?:![^"']*)?)["']""",
            script,
            flags=re.I,
        ):
            images.append(_normalize_script_image_url(value))
    return list(dict.fromkeys(image for image in images if image))


def _normalize_script_image_url(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("//"):
        return f"https:{cleaned}"
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    if cleaned.startswith("jfs/"):
        return f"https://img14.360buyimg.com/n1/{cleaned}"
    return cleaned


def _clean_price(value: str) -> str:
    cleaned = value.strip().replace(",", "")
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", cleaned)
    return match.group(0) if match else cleaned


def _json_ld_images(json_ld_blocks: list[str]) -> list[str]:
    images: list[str] = []
    for block in json_ld_blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        _collect_json_ld_images(parsed, images)
    return images


def _collect_json_ld_images(value: object, images: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_json_ld_images(item, images)
    elif isinstance(value, dict):
        image = value.get("image")
        if isinstance(image, str):
            images.append(image)
        elif isinstance(image, list):
            images.extend(str(item) for item in image if item)
        elif isinstance(image, dict) and image.get("url"):
            images.append(str(image["url"]))
        for item in value.values():
            if isinstance(item, (dict, list)):
                _collect_json_ld_images(item, images)


def _int_attr(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(float(value.replace("px", "").strip()))
    except ValueError:
        return 0
