from collections import Counter, defaultdict

from app.models import Garment

CATEGORY_LABELS = {
    "top": "上衣",
    "bottom": "下装",
    "outerwear": "外套",
    "shoes": "鞋",
    "bag": "包",
    "accessory": "配饰",
}


def build_wardrobe_report(garments: list[Garment]) -> dict[str, object]:
    ready = [garment for garment in garments if garment.status == "ready"]
    category_counts = Counter(garment.category for garment in ready)
    color_counts = Counter(color for garment in ready for color in garment.colors)
    style_counts = Counter(garment.style for garment in ready if garment.style)
    gaps = _wardrobe_gaps(category_counts)
    duplicates = _duplicate_risks(ready)
    avoid = [item["label"] for item in duplicates[:3]]
    return {
        "total": len(garments),
        "ready_total": len(ready),
        "summary": _summary(len(ready), gaps, duplicates),
        "category_distribution": _distribution(category_counts, len(ready), CATEGORY_LABELS),
        "color_distribution": _distribution(color_counts, len(ready)),
        "style_distribution": _distribution(style_counts, len(ready)),
        "scene_coverage": _scene_coverage(ready),
        "duplicate_risks": duplicates,
        "low_use_items": [],
        "wardrobe_gaps": gaps,
        "avoid_categories": avoid,
        "suggested_categories": [gap["label"] for gap in gaps[:5]],
    }


def _distribution(counter: Counter[str], total: int, labels: dict[str, str] | None = None) -> list[dict[str, object]]:
    if total == 0:
        return []
    return [
        {"key": key, "label": (labels or {}).get(key, key), "count": count, "ratio": round(count / total, 3)}
        for key, count in counter.most_common()
    ]


def _wardrobe_gaps(category_counts: Counter[str]) -> list[dict[str, object]]:
    minimums = {"top": 3, "bottom": 2, "outerwear": 1, "shoes": 2, "bag": 1, "accessory": 1}
    gaps: list[dict[str, object]] = []
    for category, minimum in minimums.items():
        count = category_counts.get(category, 0)
        if count < minimum:
            score = min(100, 70 + (minimum - count) * 10)
            gaps.append(
                {
                    "category": category,
                    "label": CATEGORY_LABELS[category],
                    "score": score,
                    "reason": f"当前只有 {count} 件，建议至少保留 {minimum} 件可轮换单品。",
                }
            )
    return sorted(gaps, key=lambda item: int(item["score"]), reverse=True)


def _duplicate_risks(garments: list[Garment]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str], list[Garment]] = defaultdict(list)
    for garment in garments:
        first_color = garment.colors[0] if garment.colors else "未设置颜色"
        groups[(garment.category, first_color)].append(garment)

    risks: list[dict[str, object]] = []
    for (category, color), items in groups.items():
        if len(items) >= 2:
            risks.append(
                {
                    "category": category,
                    "label": f"{color}{CATEGORY_LABELS.get(category, category)}",
                    "count": len(items),
                    "garment_ids": [item.id for item in items],
                }
            )
    return sorted(risks, key=lambda item: int(item["count"]), reverse=True)


def _scene_coverage(garments: list[Garment]) -> dict[str, int]:
    text = " ".join(" ".join([garment.style, *garment.tags]) for garment in garments).lower()
    return {
        "work": min(100, text.count("work") * 20 + text.count("通勤") * 20),
        "casual": min(100, text.count("casual") * 20 + text.count("休闲") * 20 + len(garments) * 5),
        "sport": min(100, text.count("sport") * 25 + text.count("运动") * 25),
        "date": min(100, text.count("date") * 25 + text.count("约会") * 25),
    }


def _summary(total: int, gaps: list[dict[str, object]], duplicates: list[dict[str, object]]) -> str:
    if total == 0:
        return "先上传 3 件常穿衣物，就能看到重复风险和衣橱缺口。"
    if duplicates:
        return f"当前衣柜已有 {total} 件，{duplicates[0]['label']} 有重复风险。"
    if gaps:
        return f"当前衣柜已有 {total} 件，最值得补充的是{gaps[0]['label']}。"
    return f"当前衣柜已有 {total} 件，基础结构较均衡。"
