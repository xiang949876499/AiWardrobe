from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

import httpx

from app.commerce import CommerceProduct
from app.config import Settings


class DemoTaobaoClient:
    def search(self, keywords: list[str], limit: int = 9) -> list[CommerceProduct]:
        seeds = keywords or ["wardrobe basic"]
        products: list[CommerceProduct] = []
        for index in range(limit):
            keyword = seeds[index % len(seeds)]
            item_id = hashlib.sha1(f"{keyword}:{index}".encode("utf-8")).hexdigest()[:12]
            title = _title_for(keyword, index)
            products.append(
                CommerceProduct(
                    platform="taobao",
                    platform_item_id=item_id,
                    title=title,
                    image_url=f"https://img.alicdn.example.com/demo/{item_id}.jpg",
                    price=f"{119 + index * 18:.2f}",
                    shop_name=f"Demo Taobao Shop {index + 1}",
                    product_url=f"https://item.taobao.com/item.htm?id={item_id}",
                    raw={"demo": True, "keyword": keyword, "rank": index + 1},
                )
            )
        return products


class TaobaoClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def build_search_params(self, keywords: list[str], limit: int = 20) -> dict[str, object]:
        query = " ".join(keywords[:3]) or "wardrobe basic"
        params: dict[str, object] = {
            "method": "taobao.tbk.dg.material.optional",
            "app_key": self.settings.taobao_app_key or "",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "2.0",
            "sign_method": "hmac",
            "q": query,
            "adzone_id": self.settings.taobao_adzone_id or "",
            "page_size": min(max(limit, 1), 100),
        }
        params["sign"] = self._sign(params)
        return params

    def search(self, keywords: list[str], limit: int = 20) -> list[CommerceProduct]:
        if not self.settings.taobao_app_key or not self.settings.taobao_app_secret or not self.settings.taobao_adzone_id:
            raise TaobaoClientError("taobao_not_configured")
        try:
            response = httpx.get(
                self.settings.taobao_api_base_url,
                params=self.build_search_params(keywords, limit),
                timeout=15,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TaobaoClientError("taobao_fetch_failed") from exc
        products = parse_taobao_products(response.json())
        if not products:
            raise TaobaoClientError("taobao_fetch_failed")
        return products

    def _sign(self, params: dict[str, object]) -> str:
        secret = self.settings.taobao_app_secret or ""
        payload = "".join(f"{key}{params[key]}" for key in sorted(params) if key != "sign")
        return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest().upper()


class TaobaoClientError(Exception):
    pass


def get_taobao_client(settings: Settings) -> DemoTaobaoClient | TaobaoClient:
    if settings.shopping_recommendation_demo_mode:
        return DemoTaobaoClient()
    return TaobaoClient(settings)


def parse_taobao_products(payload: dict[str, object]) -> list[CommerceProduct]:
    response = _dict(payload.get("tbk_dg_material_optional_response"))
    result_list = _dict(response.get("result_list"))
    rows = result_list.get("map_data") or []
    if isinstance(rows, dict):
        rows = [rows]
    products: list[CommerceProduct] = []
    for row in rows if isinstance(rows, list) else []:
        item = _dict(row)
        item_id = str(item.get("num_iid") or item.get("item_id") or "").strip()
        title = str(item.get("title") or item.get("short_title") or "").strip()
        image_url = _absolute_url(str(item.get("pict_url") or item.get("small_images") or ""))
        price = str(item.get("zk_final_price") or item.get("reserve_price") or "").strip()
        shop_name = str(item.get("shop_title") or item.get("nick") or "").strip()
        product_url = _absolute_url(str(item.get("item_url") or item.get("url") or item.get("click_url") or ""))
        if not product_url and item_id:
            product_url = f"https://item.taobao.com/item.htm?id={item_id}"
        if not item_id or not title or not image_url or not product_url:
            continue
        products.append(
            CommerceProduct(
                platform="taobao",
                platform_item_id=item_id,
                title=title,
                image_url=image_url,
                price=price,
                shop_name=shop_name,
                product_url=product_url,
                raw=item,
            )
        )
    return products


def _title_for(keyword: str, index: int) -> str:
    prefixes = ["Black", "Ivory", "Navy", "Soft", "Tailored", "Lightweight"]
    suffix = keyword.title()
    return f"{prefixes[index % len(prefixes)]} {suffix}"


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _absolute_url(value: str) -> str:
    clean = value.strip()
    if clean.startswith("//"):
        return f"https:{clean}"
    return clean
