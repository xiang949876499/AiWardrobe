from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommerceProduct:
    platform: str
    platform_item_id: str
    title: str
    image_url: str
    price: str
    shop_name: str
    product_url: str
    raw: dict[str, object] = field(default_factory=dict)
