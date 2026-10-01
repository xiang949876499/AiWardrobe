"""Regenerate frontend fixtures from the actual validated purchase contract.

Run from any directory: python backend/scripts/generate_purchase_contract_fixtures.py
No database, network or provider calls are made.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import AiAnalysis
from app.models import Garment
from app.purchase_analysis import analyze_purchase
from app.schemas import PurchaseAnalysisDetail


def main() -> None:
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    candidate = AiAnalysis(
        category="top", colors=["black"], style="casual", material="cotton",
        season=["summer"], fit="regular", tags=["basic"], confidence=0.9, raw={},
    )
    wardrobe = [
        Garment(
            id=f"fixture-{category}", user_id="test-user", category=category,
            colors=["black"], style="casual", material="cotton", season=["summer"],
            fit="regular", tags=["basic"], status="ready", review_status="confirmed",
            user_confirmed_at=now, image_url="/v2/assets/test/content", thumbnail_url=None,
        )
        for category in ["top", "bottom", "shoes"]
    ]
    cases = [
        ("normal", wardrobe, {}, "129.00"),
        ("insufficient", [], {}, None),
        ("hard_constraint", wardrobe, {"budget_max": "100"}, "129.00"),
        ("unknown_price", wardrobe, {"budget_max": "100"}, None),
    ]
    fixtures = {}
    for name, garments, preferences, price in cases:
        result = analyze_purchase(
            candidate, garments, preferences, actual_price=price, currency="CNY",
            candidate_id="candidate-1", user_id="test-user",
        )
        fixtures[name] = {
            "score": result.score, "recommendation": result.recommendation,
            "reason_summary": result.reason_summary,
            "analysis": PurchaseAnalysisDetail.model_validate(result.analysis).model_dump(mode="json"),
            "actual_price": price, "coverage": result.coverage, "evidence": result.evidence,
        }
    destination = ROOT / "frontend" / "src" / "test" / "purchase-contract-fixtures.json"
    destination.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(fixtures)} purchase contract cases: {destination}")


if __name__ == "__main__":
    main()
