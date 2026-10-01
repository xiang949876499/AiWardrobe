from datetime import datetime, timezone

import pytest

from app.ai import AiAnalysis
from app.models import Garment
from app.purchase_analysis import analyze_purchase
from app.schemas import PurchaseAnalysisDetail, _normalize_purchase_analysis


def candidate(category="top", season=None):
    return AiAnalysis(category=category, colors=["black"], style="casual", material="cotton",
                      season=["summer"] if season is None else season, fit="regular", tags=[],
                      confidence=0.9, raw={})


def garment(category, suffix="1", season=None, **overrides):
    values = dict(id=f"{category}-{suffix}", user_id="owner", category=category,
                  image_url=f"/v2/assets/{category}-{suffix}/content", image_key="",
                  colors=["black"], style="casual", material="cotton", fit="regular", tags=[],
                  season=["summer"] if season is None else season, status="ready",
                  review_status="confirmed", user_confirmed_at=datetime.now(timezone.utc))
    values.update(overrides)
    return Garment(**values)


def test_only_bag_is_not_evidence_of_top_pairing_or_precise_total_score():
    decision = analyze_purchase(candidate(), [garment("bag")])
    assert decision.coverage["status"] == "insufficient"
    assert decision.coverage["dimensions"]["outfit_potential"] is False
    assert decision.analysis["dimensions"]["outfit_potential"] is None
    assert decision.score is None
    normalized = _normalize_purchase_analysis(decision.analysis, {"similar_items": []})
    assert normalized["duplicate_score"] == decision.analysis["duplicate_score"]
    assert normalized["score_breakdown"] == decision.analysis["score_breakdown"]
    assert decision.analysis["outfit_ideas"] == []


def test_same_category_evidence_remains_available_when_pairing_evidence_missing():
    decision = analyze_purchase(candidate(), [garment("top")])
    assert decision.coverage["dimensions"]["duplicate_risk"] is True
    assert decision.analysis["dimensions"]["duplicate_risk"] >= 80
    assert decision.analysis["dimensions"]["outfit_potential"] is None
    assert decision.analysis["dimensions"]["idle_risk"] is None
    assert decision.score is None
    normalized = _normalize_purchase_analysis(decision.analysis, {"similar_items": []})
    assert normalized["duplicate_score"] >= 80
    assert normalized["score_breakdown"]["outfit_potential"] is None


def test_real_combinations_have_unique_categories_ids_and_at_most_three_groups():
    wardrobe = [garment(category, str(i)) for category in
                ["top", "bottom", "shoes", "outerwear", "bag", "accessory"] for i in range(3)]
    decision = analyze_purchase(candidate(), wardrobe, candidate_id="candidate", user_id="owner")
    groups = decision.analysis["outfit_ideas"]
    assert len(groups) == 3
    signatures = set()
    for group in groups:
        categories = [item["category"] for item in group["items"]]
        assert {"top", "bottom", "shoes"} <= set(categories)
        assert len(categories) == len(set(categories))
        assert [item["candidate_id"] for item in group["items"] if "candidate_id" in item] == ["candidate"]
        owned = [item for item in group["items"] if "garment_id" in item]
        assert all(item["garment_id"] in {g.id for g in wardrobe} and item["image_url"] for item in owned)
        signatures.add(tuple(item.get("garment_id", "candidate") for item in group["items"]))
        assert group["seasons"] == ["summer"]
    assert len(signatures) == 3
    assert PurchaseAnalysisDetail.model_validate(decision.analysis).outfit_ideas == groups
    assert decision.coverage["status"] == "sufficient"
    assert isinstance(decision.score, int)


@pytest.mark.parametrize("category", ["top", "bottom", "shoes", "outerwear", "bag", "accessory"])
def test_each_supported_candidate_category_can_participate_in_a_complete_outfit(category):
    decision = analyze_purchase(candidate(category), [garment(c) for c in ["top", "bottom", "shoes"]],
                                candidate_id="candidate", user_id="owner")
    assert decision.analysis["outfit_ideas"]
    assert any(item.get("candidate_id") == "candidate" for item in decision.analysis["outfit_ideas"][0]["items"])


def test_unconfirmed_foreign_excluded_and_avoided_garments_cannot_enter_combinations():
    wardrobe = [garment("bottom", "pending", review_status="pending_review"),
                garment("bottom", "foreign", user_id="other"),
                garment("bottom", "excluded"), garment("bottom", "valid"), garment("shoes"), garment("bag")]
    decision = analyze_purchase(candidate(), wardrobe, {"avoid_categories": ["bag"]},
                                candidate_id="candidate", user_id="owner", excluded_garment_ids={"bottom-excluded"})
    assert decision.analysis["outfit_ideas"]
    owned = {item["garment_id"] for group in decision.analysis["outfit_ideas"] for item in group["items"] if "garment_id" in item}
    assert owned == {"bottom-valid", "shoes-1"}
    assert "bottom-foreign" not in decision.evidence["confirmed_garment_ids"]


def test_missing_candidate_id_never_creates_an_unidentified_template():
    decision = analyze_purchase(candidate(), [garment("bottom"), garment("shoes")])
    assert decision.analysis["outfit_ideas"] == []
    assert decision.analysis["outfit_potential"] is None


def test_pairwise_season_overlap_is_insufficient_without_global_intersection():
    decision = analyze_purchase(candidate(season=["spring", "summer"]),
                                [garment("bottom", season=["summer", "autumn"]),
                                 garment("shoes", season=["spring", "autumn"])], candidate_id="candidate")
    assert decision.analysis["outfit_ideas"] == []
    assert decision.analysis["outfit_potential"] == 0


def test_unknown_season_has_warning_and_is_not_precise_matching_evidence():
    decision = analyze_purchase(candidate(), [garment("bottom", season=[]), garment("shoes")],
                                candidate_id="candidate")
    assert decision.analysis["outfit_ideas"]
    group = decision.analysis["outfit_ideas"][0]
    assert "season_unverified" in group["warnings"]
    assert group["seasons"] == []
    assert decision.analysis["outfit_potential"] is None


def test_explicit_preferences_exclusions_and_avoided_required_category_allow_zero_groups():
    wardrobe = [garment("bottom"), garment("shoes")]
    excluded = analyze_purchase(candidate(), wardrobe, {"excluded_garment_ids": ["shoes-1"]}, candidate_id="candidate")
    avoided = analyze_purchase(candidate(), wardrobe, {"avoid_categories": ["shoes"]}, candidate_id="candidate")
    assert excluded.analysis["outfit_ideas"] == []
    assert avoided.analysis["outfit_ideas"] == []


def test_unknown_and_all_seasons_labels_are_not_treated_as_literal_matching_seasons():
    unknown = analyze_purchase(candidate(), [garment("bottom", season=["unknown"]), garment("shoes")], candidate_id="candidate")
    assert unknown.analysis["outfit_ideas"][0]["warnings"] == ["season_unverified"]
    all_seasons = analyze_purchase(candidate(), [garment("bottom", season=["all"]), garment("shoes")], candidate_id="candidate")
    assert all_seasons.analysis["outfit_ideas"][0]["seasons"] == ["summer"]


def test_private_assets_are_referenced_without_empty_image_placeholders():
    decision = analyze_purchase(candidate(),
                                [garment("bottom", image_url="", asset_id="bottom-asset"),
                                 garment("shoes", image_url="", asset_id="shoes-asset")], candidate_id="candidate")
    owned = [item for item in decision.analysis["outfit_ideas"][0]["items"] if "garment_id" in item]
    assert {item.get("asset_id") for item in owned} == {"bottom-asset", "shoes-asset"}
    assert all("image_url" not in item for item in owned)


@pytest.mark.parametrize("response_kind", ["v2", "legacy"])
def test_candidate_responses_hydrate_owned_images_hide_invalid_refs_and_preserve_snapshot(client, response_kind):
    from app.asset_service import candidate_response, create_image_asset
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import PurchaseAnalysisSnapshot, PurchaseCandidate, User
    from tests.conftest import jpeg_bytes, login
    from tests.test_decision_api import _candidate

    token = login(client, "outfit-refs@example.com")
    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="outfit-refs@example.com").one()
        asset = create_image_asset(db, get_settings(), user.id, jpeg_bytes(), "image/jpeg").original
        owned = [garment(category, user_id=user.id, image_url="", asset_id=asset.id) for category in ["bottom", "shoes"]]
        db.add_all(owned)
        db.flush()
        item = _candidate(db, user.id)
        item.asset_id = asset.id
        decision = analyze_purchase(candidate(), owned, candidate_id=item.id, user_id=user.id)
        # An old/deleted/foreign ref or forged candidate ref must never get a signed URL.
        groups = decision.analysis["outfit_ideas"]
        valid_group = groups[0]
        bad_owned = {**valid_group, "items": [
            {**piece, "garment_id": "not-owned", "image_url": "https://untrusted.example/image"}
            if piece.get("category") == "bottom" else piece for piece in valid_group["items"]]}
        bad_candidate = {**valid_group, "items": [
            {**piece, "candidate_id": "other-candidate"} if "candidate_id" in piece else piece for piece in valid_group["items"]]}
        item.analysis = {**decision.analysis, "outfit_ideas": [valid_group, bad_owned, bad_candidate]}
        snapshot = db.query(PurchaseAnalysisSnapshot).filter_by(candidate_id=item.id).one()
        snapshot.decision = {**snapshot.decision, "analysis": item.analysis}
        db.commit()
        item_id = item.id
        if response_kind == "legacy":
            payload = candidate_response(db, item, get_settings())
        else:
            payload = None
    if response_kind == "v2":
        result = client.get(f"/v2/purchase/candidates/{item_id}", headers={"Authorization": f"Bearer {token}"})
        assert result.status_code == 200
        payload = result.json()
        assert len(payload["snapshots"][0]["decision"]["analysis"]["outfit_ideas"]) == 1
    hydrated = payload["analysis"]["outfit_ideas"]
    assert len(hydrated) == 1
    assert all("asset_token=" in piece["image_url"] for piece in hydrated[0]["items"])
    with get_session_factory()() as db:
        snapshot = db.query(PurchaseAnalysisSnapshot).filter_by(candidate_id=item_id).one()
        assert "asset_token" not in str(snapshot.decision)
        db.delete(db.get(Garment, "shoes-1"))
        db.commit()
        if response_kind == "legacy":
            assert candidate_response(db, db.get(PurchaseCandidate, item_id), get_settings())["analysis"]["outfit_ideas"] == []
    if response_kind == "v2":
        result = client.get(f"/v2/purchase/candidates/{item_id}", headers={"Authorization": f"Bearer {token}"})
        assert result.json()["analysis"]["outfit_ideas"] == []


@pytest.mark.parametrize("version, expected", [
    ("purchase-v2-combinations", "purchase-v2-combinations"), (None, "purchase-v1"),
    ("", "purchase-v1"), (42, "purchase-v1"), ("x" * 41, "purchase-v1"),
    ("invalid version", "purchase-v1"),
])
def test_snapshot_rule_version_follows_validated_decision_evidence(client, version, expected):
    from app.database import get_session_factory
    from app.decision_context import create_purchase_snapshot
    from app.models import User
    from tests.conftest import login
    from tests.test_decision_api import _candidate

    login(client, "snapshot-version@example.com")
    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="snapshot-version@example.com").one()
        item = _candidate(db, user.id)
        # Explicit worker payload is the generation's evidence, even if the
        # candidate's currently published analysis still belongs to version 1.
        decision = {"analysis": {"coverage": {}, "evidence": {"rule_version": version}}}
        snapshot = create_purchase_snapshot(db, item, snapshot_revision=2, decision_payload=decision)
        assert snapshot.rule_version == expected
        assert snapshot.snapshot_revision == 2
        assert snapshot.evidence == decision["analysis"]["evidence"]
        replay = create_purchase_snapshot(db, item, snapshot_revision=2,
                                          decision_payload={"analysis": {"evidence": {"rule_version": "other"}}})
        assert replay.id == snapshot.id
        assert replay.rule_version == expected


def test_old_rule_snapshots_are_readable_but_stale_until_reanalysis(client):
    from app.database import get_session_factory
    from app.decision_context import create_purchase_snapshot
    from app.models import User
    from tests.conftest import login
    from tests.test_decision_api import _candidate

    token = login(client, "stale-rule@example.com")
    with get_session_factory()() as db:
        user = db.query(User).filter_by(email="stale-rule@example.com").one()
        item = _candidate(db, user.id)
        item_id = item.id
        db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    old = client.get(f"/v2/purchase/candidates/{item_id}", headers=headers).json()
    assert old["stale"] is True
    assert old["snapshots"][0]["stale"] is True
    with get_session_factory()() as db:
        from app.models import PurchaseCandidate
        item = db.get(PurchaseCandidate, item_id)
        item.requested_snapshot_revision = 2
        item.analysis = analyze_purchase(candidate(), [], candidate_id=item.id, user_id=item.user_id).analysis
        create_purchase_snapshot(db, item, snapshot_revision=2)
        db.commit()
    new = client.get(f"/v2/purchase/candidates/{item_id}", headers=headers).json()
    assert new["stale"] is False
    assert [snapshot["stale"] for snapshot in new["snapshots"]] == [False, True]


@pytest.mark.parametrize("has_late_match", [False, True])
def test_large_wardrobe_prunes_incompatible_seasons_without_losing_late_matches(monkeypatch, has_late_match):
    import app.purchase_analysis as rules

    wardrobe = [garment(category, f"{i:04}", season=[season])
                for category, season in [("top", "summer"), ("bottom", "winter"), ("shoes", "winter")]
                for i in range(400)]
    if has_late_match:
        wardrobe.extend([garment("bottom", "zz-valid", season=["summer"]),
                         garment("shoes", "zz-valid", season=["summer"])])
    evaluations = 0
    real_season_evidence = rules._season_evidence

    def bounded_season_evidence(items):
        nonlocal evaluations
        evaluations += 1
        assert evaluations <= 100, "Season pruning must avoid evaluating the invalid Cartesian product"
        return real_season_evidence(items)

    monkeypatch.setattr(rules, "_season_evidence", bounded_season_evidence)
    decision = analyze_purchase(candidate("bag"), wardrobe, candidate_id="candidate", user_id="owner")
    groups = decision.analysis["outfit_ideas"]
    assert len(groups) == (3 if has_late_match else 0)
    for group in groups:
        owned_ids = {item.get("garment_id") for item in group["items"]}
        assert {"bottom-zz-valid", "shoes-zz-valid"} <= owned_ids
        assert group["seasons"] == ["summer"]
