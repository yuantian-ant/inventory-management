import threading

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str
    category: Optional[str] = None
    unit_cost: Optional[float] = None

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

class RestockRecommendation(BaseModel):
    item_sku: str
    item_name: str
    category: str
    trend: str
    current_supply: int
    forecasted_demand: int
    recommended_quantity: int
    unit_cost: float
    line_cost: float
    lead_time_days: int
    clipped: bool  # True when budget only covered part of the demand gap

class RestockOrderItemRequest(BaseModel):
    sku: str
    # Bounded to keep totals sane; prices are never accepted from the client
    quantity: int = Field(gt=0, le=100_000)

class CreateRestockOrderRequest(BaseModel):
    items: List[RestockOrderItemRequest]

class RestockOrderItem(BaseModel):
    sku: str
    name: str
    category: str
    quantity: int
    unit_cost: float
    line_cost: float

class RestockOrder(BaseModel):
    id: str
    order_number: str
    status: str
    items: List[RestockOrderItem]
    total_value: float
    lead_time_days: int
    order_date: str
    expected_delivery: str

class RestockRecommendationsResponse(BaseModel):
    budget: float
    total_cost: float
    remaining_budget: float
    recommendations: List[RestockRecommendation]

# Fixed supplier lead time per category (days); an order's lead time is the
# longest lead time among its items since everything ships together
CATEGORY_LEAD_TIMES = {
    'Actuators': 10,
    'Circuit Boards': 14,
    'Controllers': 12,
    'Power Supplies': 7,
    'Sensors': 5,
}
DEFAULT_LEAD_TIME_DAYS = 7

# Submitted restocking orders live in memory only (reset on restart, like all demo data).
# Sync endpoints run on a threadpool, so order-number allocation must be serialized —
# deriving numbers from len() outside a lock would mint duplicates under concurrent POSTs
submitted_restock_orders: list = []
_restock_orders_lock = threading.Lock()

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/restock/recommendations", response_model=RestockRecommendationsResponse)
def get_restock_recommendations(budget: float = Query(..., ge=0, le=1_000_000)):
    """Recommend demand-forecast items to restock within the given budget.

    Urgency-ranked greedy: items with an increasing trend come first, then by the
    size of the gap between forecasted demand and current supply. Items are added
    in that order until the budget runs out; the last item's quantity is clipped
    to whatever the remaining budget can afford.
    """
    inventory_by_sku = {item["sku"]: item for item in inventory_items}

    candidates = []
    for forecast in demand_forecasts:
        unit_cost = forecast.get("unit_cost")
        # `is None`, not falsy: a legitimate zero-cost catalog entry must not vanish
        if unit_cost is None:
            continue
        stock = inventory_by_sku.get(forecast["item_sku"])
        # Most forecast SKUs are not tracked in inventory; for those, current
        # demand acts as the supply proxy so the gap measures demand growth
        current_supply = stock["quantity_on_hand"] if stock else forecast["current_demand"]
        gap = forecast["forecasted_demand"] - current_supply
        if gap <= 0:
            continue
        candidates.append({
            "item_sku": forecast["item_sku"],
            "item_name": forecast["item_name"],
            "category": forecast.get("category", ""),
            "trend": forecast["trend"],
            "current_supply": current_supply,
            "forecasted_demand": forecast["forecasted_demand"],
            "gap": gap,
            "unit_cost": unit_cost,
        })

    # False sorts before True, so increasing-trend items rank first
    candidates.sort(key=lambda c: (c["trend"] != "increasing", -c["gap"]))

    recommendations = []
    remaining = budget
    for c in candidates:
        quantity = c["gap"]
        clipped = False
        if quantity * c["unit_cost"] > remaining:
            quantity = int(remaining // c["unit_cost"])
            if quantity <= 0:
                continue
            clipped = True
        line_cost = round(quantity * c["unit_cost"], 2)
        remaining = round(remaining - line_cost, 2)
        recommendations.append(RestockRecommendation(
            item_sku=c["item_sku"],
            item_name=c["item_name"],
            category=c["category"],
            trend=c["trend"],
            current_supply=c["current_supply"],
            forecasted_demand=c["forecasted_demand"],
            recommended_quantity=quantity,
            unit_cost=c["unit_cost"],
            line_cost=line_cost,
            lead_time_days=CATEGORY_LEAD_TIMES.get(c["category"], DEFAULT_LEAD_TIME_DAYS),
            clipped=clipped,
        ))

    total_cost = round(sum(r.line_cost for r in recommendations), 2)
    return {
        "budget": budget,
        "total_cost": total_cost,
        "remaining_budget": round(budget - total_cost, 2),
        "recommendations": recommendations,
    }

@app.post("/api/restock/orders", response_model=RestockOrder, status_code=201)
def create_restock_order(request: CreateRestockOrderRequest):
    """Submit a restocking order for demand-forecast items"""
    if not request.items:
        raise HTTPException(status_code=400, detail="Order must contain at least one item")

    forecast_by_sku = {f["item_sku"]: f for f in demand_forecasts}

    order_items = []
    total_value = 0.0
    lead_time_days = 0
    for line in request.items:
        forecast = forecast_by_sku.get(line.sku)
        # Name, category, and price always come from the server-side catalog —
        # the client only chooses SKUs and quantities
        if not forecast or forecast.get("unit_cost") is None:
            raise HTTPException(status_code=400, detail=f"Unknown restock SKU: {line.sku}")
        category = forecast.get("category", "")
        line_cost = round(line.quantity * forecast["unit_cost"], 2)
        total_value += line_cost
        lead_time_days = max(lead_time_days, CATEGORY_LEAD_TIMES.get(category, DEFAULT_LEAD_TIME_DAYS))
        order_items.append({
            "sku": line.sku,
            "name": forecast["item_name"],
            "category": category,
            "quantity": line.quantity,
            "unit_cost": forecast["unit_cost"],
            "line_cost": line_cost,
        })

    now = datetime.now()
    with _restock_orders_lock:
        order_seq = len(submitted_restock_orders) + 1
        order = {
            "id": f"restock-{order_seq}",
            "order_number": f"RST-2025-{order_seq:04d}",
            "status": "Submitted",
            "items": order_items,
            "total_value": round(total_value, 2),
            "lead_time_days": lead_time_days,
            "order_date": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "expected_delivery": (now + timedelta(days=lead_time_days)).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        submitted_restock_orders.append(order)
    return order

@app.get("/api/restock/orders", response_model=List[RestockOrder])
def get_restock_orders():
    """Get submitted restocking orders (newest first)"""
    return list(reversed(submitted_restock_orders))

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports():
    """Get quarterly performance reports"""
    # Calculate quarterly statistics from orders
    quarters = {}

    for order in orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends():
    """Get month-over-month trends"""
    months = {}

    for order in orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
