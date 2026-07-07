"""
Tests for restocking API endpoints.
"""
import pytest

import main


@pytest.fixture(autouse=True)
def clean_restock_orders():
    """Restock orders live in module-level state; isolate each test."""
    main.submitted_restock_orders.clear()
    yield
    main.submitted_restock_orders.clear()


class TestRestockRecommendationsEndpoint:
    """Test suite for GET /api/restock/recommendations."""

    def test_get_recommendations_basic(self, client):
        """Test getting recommendations with a normal budget."""
        response = client.get("/api/restock/recommendations?budget=2000")
        assert response.status_code == 200

        data = response.json()
        assert data["budget"] == 2000
        assert isinstance(data["recommendations"], list)
        assert len(data["recommendations"]) > 0
        assert data["total_cost"] <= 2000
        assert abs(data["remaining_budget"] - (data["budget"] - data["total_cost"])) < 0.01

    def test_recommendation_structure(self, client):
        """Test that recommendations have proper structure and types."""
        response = client.get("/api/restock/recommendations?budget=5000")
        data = response.json()

        for rec in data["recommendations"]:
            assert "item_sku" in rec
            assert "item_name" in rec
            assert "category" in rec
            assert "trend" in rec
            assert isinstance(rec["current_supply"], int)
            assert isinstance(rec["forecasted_demand"], int)
            assert isinstance(rec["recommended_quantity"], int)
            assert rec["recommended_quantity"] > 0
            assert isinstance(rec["unit_cost"], (int, float))
            assert isinstance(rec["line_cost"], (int, float))
            assert isinstance(rec["lead_time_days"], int)
            assert isinstance(rec["clipped"], bool)
            # Line cost must equal quantity * unit cost
            assert abs(rec["line_cost"] - rec["recommended_quantity"] * rec["unit_cost"]) < 0.01

    def test_recommendations_respect_budget(self, client):
        """Test that total cost never exceeds the budget."""
        for budget in [10, 100, 1000, 4500]:
            response = client.get(f"/api/restock/recommendations?budget={budget}")
            assert response.status_code == 200
            data = response.json()
            assert data["total_cost"] <= budget

    def test_recommendations_urgency_order(self, client):
        """Test increasing-trend items are recommended before other trends."""
        response = client.get("/api/restock/recommendations?budget=1000000")
        data = response.json()
        trends = [rec["trend"] for rec in data["recommendations"]]

        # Once a non-increasing trend appears, no increasing item may follow
        seen_non_increasing = False
        for trend in trends:
            if trend != "increasing":
                seen_non_increasing = True
            else:
                assert not seen_non_increasing

    def test_only_last_affordable_item_clipped(self, client):
        """Test that at most one recommendation is clipped by the budget."""
        response = client.get("/api/restock/recommendations?budget=2000")
        data = response.json()
        clipped = [rec for rec in data["recommendations"] if rec["clipped"]]
        assert len(clipped) <= 1

    def test_zero_budget_returns_empty(self, client):
        """Test that a zero budget yields no recommendations."""
        response = client.get("/api/restock/recommendations?budget=0")
        assert response.status_code == 200

        data = response.json()
        assert data["recommendations"] == []
        assert data["total_cost"] == 0

    def test_negative_budget_rejected(self, client):
        """Test that a negative budget fails validation."""
        response = client.get("/api/restock/recommendations?budget=-5")
        assert response.status_code == 422

    def test_budget_over_maximum_rejected(self, client):
        """Test that a budget above the cap fails validation."""
        response = client.get("/api/restock/recommendations?budget=2000000")
        assert response.status_code == 422

    def test_missing_budget_rejected(self, client):
        """Test that omitting the budget fails validation."""
        response = client.get("/api/restock/recommendations")
        assert response.status_code == 422


class TestRestockOrdersEndpoints:
    """Test suite for POST and GET /api/restock/orders."""

    def test_create_restock_order(self, client):
        """Test submitting a valid restocking order."""
        response = client.post(
            "/api/restock/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 10}]},
        )
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"] == "RST-2025-0001"
        assert order["status"] == "Submitted"
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "WDG-001"
        assert order["items"][0]["quantity"] == 10
        assert "T" in order["order_date"]
        assert "T" in order["expected_delivery"]

    def test_order_prices_come_from_server_catalog(self, client):
        """Test that client-supplied prices are ignored."""
        response = client.post(
            "/api/restock/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 2, "unit_cost": 0.01}]},
        )
        assert response.status_code == 201

        order = response.json()
        # WDG-001 costs 12.50 in the demand-forecast catalog
        assert abs(order["items"][0]["unit_cost"] - 12.50) < 0.01
        assert abs(order["total_value"] - 25.00) < 0.01

    def test_order_lead_time_is_max_of_categories(self, client):
        """Test that order lead time is the longest item lead time."""
        # WDG-001 is Actuators (10 days), CTL-330 is Controllers (12 days)
        response = client.post(
            "/api/restock/orders",
            json={"items": [
                {"sku": "WDG-001", "quantity": 1},
                {"sku": "CTL-330", "quantity": 1},
            ]},
        )
        assert response.status_code == 201
        assert response.json()["lead_time_days"] == 12

    def test_create_order_unknown_sku(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post(
            "/api/restock/orders",
            json={"items": [{"sku": "FAKE-999", "quantity": 5}]},
        )
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "FAKE-999" in data["detail"]

    def test_create_order_empty_items(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restock/orders", json={"items": []})
        assert response.status_code == 400

    def test_create_order_zero_quantity(self, client):
        """Test that a zero quantity fails validation."""
        response = client.post(
            "/api/restock/orders",
            json={"items": [{"sku": "WDG-001", "quantity": 0}]},
        )
        assert response.status_code == 422

    def test_get_restock_orders_newest_first(self, client):
        """Test that submitted orders are listed newest first."""
        client.post("/api/restock/orders", json={"items": [{"sku": "WDG-001", "quantity": 1}]})
        client.post("/api/restock/orders", json={"items": [{"sku": "FLT-405", "quantity": 1}]})

        response = client.get("/api/restock/orders")
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 2
        assert data[0]["order_number"] == "RST-2025-0002"
        assert data[1]["order_number"] == "RST-2025-0001"

    def test_get_restock_orders_empty(self, client):
        """Test listing orders when none have been submitted."""
        response = client.get("/api/restock/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_order_numbers_are_sequential_and_unique(self, client):
        """Test that consecutive orders get distinct sequential numbers."""
        numbers = []
        for _ in range(3):
            response = client.post(
                "/api/restock/orders",
                json={"items": [{"sku": "WDG-001", "quantity": 1}]},
            )
            numbers.append(response.json()["order_number"])

        assert numbers == ["RST-2025-0001", "RST-2025-0002", "RST-2025-0003"]
        assert len(set(numbers)) == 3
