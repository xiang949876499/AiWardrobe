# Shopping Recommendations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Shopping Recommendations page that searches Taobao/Tmall demo or configured products, analyzes useful candidates against the user's wardrobe, and lets the user save recommendations through the existing purchase-candidate flow.

**Architecture:** Add focused backend modules for normalized commerce products, Taobao/demo search, wardrobe keyword generation, rate limits, and recommendation orchestration. Persist recommendation runs/items and expose `/shopping/recommendations` APIs, then add frontend API types and a product-stream page wired into existing navigation.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, React, TypeScript, Vitest, Testing Library.

---

### Task 1: Backend Recommendation Data Model

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/test_shopping_recommendations.py`

- [ ] **Step 1: Write the failing model/schema/API test**

```python
def test_demo_recommendations_create_run_and_items(client: TestClient) -> None:
    token = login(client)
    client.post(
        "/uploads/plain-garment",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("white-shirt.jpg", b"existing image", "image/jpeg")},
    )

    response = client.post(
        "/shopping/recommendations",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "auto_gap", "refresh": True},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["target"] == "auto_gap"
    assert body["status"] == "ready"
    assert body["keywords"]
    assert body["items"]
    assert body["items"][0]["platform"] == "taobao"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_shopping_recommendations.py::test_demo_recommendations_create_run_and_items -q`
Expected: FAIL because `/shopping/recommendations` is not registered.

- [ ] **Step 3: Add model and schema definitions**

Add `ShoppingRecommendationRun` and `ShoppingRecommendationItem` models, user relationships, Pydantic request/response schemas, and lightweight column safeguards for existing databases.

- [ ] **Step 4: Run test to keep failure focused**

Run: `python -m pytest backend/tests/test_shopping_recommendations.py::test_demo_recommendations_create_run_and_items -q`
Expected: still FAIL because the route is not implemented yet.

### Task 2: Commerce Search, Keywords, and Limits

**Files:**
- Create: `backend/app/commerce.py`
- Create: `backend/app/taobao_client.py`
- Create: `backend/app/shopping_recommendations.py`
- Create: `backend/app/rate_limit.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_shopping_recommendations.py`

- [ ] **Step 1: Write failing unit tests**

```python
def test_auto_gap_keywords_prioritize_missing_categories() -> None:
    from app.shopping_recommendations import generate_recommendation_keywords

    garments = [
        Garment(user_id="u", image_url="/top.jpg", image_key="top.jpg", category="top", colors=["white"], style="work", material="cotton", season=["summer"], status="ready"),
        Garment(user_id="u", image_url="/bottom.jpg", image_key="bottom.jpg", category="bottom", colors=["black"], style="work", material="cotton", season=["summer"], status="ready"),
    ]

    keywords = generate_recommendation_keywords("auto_gap", garments)

    assert keywords
    assert any("shoe" in keyword or "bag" in keyword for keyword in keywords)


def test_demo_taobao_client_returns_normalized_products() -> None:
    from app.taobao_client import DemoTaobaoClient

    products = DemoTaobaoClient().search(["work skirt"])

    assert products[0].platform == "taobao"
    assert products[0].platform_item_id
    assert products[0].product_url.startswith("https://")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_shopping_recommendations.py::test_auto_gap_keywords_prioritize_missing_categories backend/tests/test_shopping_recommendations.py::test_demo_taobao_client_returns_normalized_products -q`
Expected: FAIL because modules are missing.

- [ ] **Step 3: Implement minimal modules**

Implement `CommerceProduct`, demo product search, deterministic keyword generation, and in-memory/database-backed window limit helpers used by the router.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_shopping_recommendations.py::test_auto_gap_keywords_prioritize_missing_categories backend/tests/test_shopping_recommendations.py::test_demo_taobao_client_returns_normalized_products -q`
Expected: PASS.

### Task 3: Shopping Recommendation API

**Files:**
- Create: `backend/app/routers/shopping.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/shopping_recommendations.py`
- Test: `backend/tests/test_shopping_recommendations.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_recommendations_reuse_cache_when_refresh_false(client: TestClient) -> None:
    token = login(client)
    first = client.post("/shopping/recommendations", headers={"Authorization": f"Bearer {token}"}, json={"target": "work", "refresh": True}).json()
    second = client.post("/shopping/recommendations", headers={"Authorization": f"Bearer {token}"}, json={"target": "work", "refresh": False}).json()

    assert second["id"] == first["id"]
    assert second["cache_hit"] is True


def test_analyze_item_creates_purchase_candidate(client: TestClient) -> None:
    token = login(client)
    run = client.post("/shopping/recommendations", headers={"Authorization": f"Bearer {token}"}, json={"target": "basics", "refresh": True}).json()
    pending = next(item for item in run["items"] if item["analysis_status"] == "pending_analysis")

    analyzed = client.post(f"/shopping/recommendations/items/{pending['id']}/analyze", headers={"Authorization": f"Bearer {token}"})

    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["analysis_status"] == "analyzed"
    assert body["purchase_candidate_id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_shopping_recommendations.py::test_recommendations_reuse_cache_when_refresh_false backend/tests/test_shopping_recommendations.py::test_analyze_item_creates_purchase_candidate -q`
Expected: FAIL because the router/orchestrator is incomplete.

- [ ] **Step 3: Implement router and orchestration**

Register `shopping.router`, create runs/items, reuse recent runs for `refresh=false`, auto-analyze top candidates, and expose item analysis with ownership checks.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_shopping_recommendations.py -q`
Expected: PASS.

### Task 4: Frontend API and Product Stream

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write failing frontend test**

```ts
test("shopping recommendations loads products and saves analyzed items", async () => {
  const user = userEvent.setup();
  localStorage.setItem("aiwardrobe_token", "token");
  mockFetchOnce({ items: [garment] });
  mockFetchOnce(shoppingRun, 201);
  mockFetchOnce(savedGarment, 201);

  render(<App />);

  await screen.findByRole("button", { name: "衣橱" });
  await user.click(screen.getByRole("button", { name: "推荐购买" }));
  await user.click(screen.getByRole("button", { name: "获取推荐" }));

  expect(await screen.findByText("Black Work Skirt")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "加入衣橱" }));

  await waitFor(() => expect(String(vi.mocked(fetch).mock.calls[2][0])).toBe("/purchase/candidates/candidate-shopping-1/save"));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- App.test.tsx -t "shopping recommendations loads products"`
Expected: FAIL because the page and API functions do not exist.

- [ ] **Step 3: Implement frontend types, API functions, nav entry, and page**

Add shopping recommendation types, `createShoppingRecommendations`, `analyzeShoppingRecommendationItem`, nav item, `ShoppingRecommendationsView`, card actions, status labels, and responsive CSS.

- [ ] **Step 4: Run frontend tests**

Run: `npm test -- App.test.tsx -t "shopping recommendations loads products"`
Expected: PASS.

### Task 5: Environment Docs and Full Verification

**Files:**
- Modify: `.env.example`
- Optional Modify: `README.md`

- [ ] **Step 1: Add Taobao/demo environment variables**

Add `TAOBAO_APP_KEY`, `TAOBAO_APP_SECRET`, `TAOBAO_ADZONE_ID`, `TAOBAO_API_BASE_URL`, and `SHOPPING_RECOMMENDATION_DEMO_MODE`.

- [ ] **Step 2: Run backend test suite**

Run: `python -m pytest`
Expected: all backend tests pass.

- [ ] **Step 3: Run frontend test suite**

Run: `npm test`
Expected: all frontend tests pass.

- [ ] **Step 4: Run frontend build**

Run: `npm run build`
Expected: TypeScript and Vite build pass.

- [ ] **Step 5: Run whitespace check**

Run: `git diff --check`
Expected: no whitespace errors.

---

## Self-Review

- Spec coverage: model persistence, Taobao/demo provider, keyword generation, cache, limits, backend APIs, frontend product stream, save-to-wardrobe reuse, and verification are covered by Tasks 1-5.
- Placeholder scan: no `TBD`, `TODO`, `implement later`, or unspecified test steps are present.
- Type consistency: backend targets use `auto_gap`, `work`, `date`, `sport`, `summer`, `basics`; item analysis statuses use `pending_analysis`, `analyzing`, `analyzed`, `failed`; frontend mirrors those names.
