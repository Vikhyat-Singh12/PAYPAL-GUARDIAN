from decimal import Decimal
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.payment_request import PaymentRequest
from tests.conftest import MockPayPalTransport


@pytest.mark.asyncio
async def test_milestone_2_approve_1000_calls_paypal(
    async_client: httpx.AsyncClient,
    mock_paypal_transport: MockPayPalTransport,
    db_session: AsyncSession,
):
    """
    Milestone 2 Core Specification:
    ₹1,000 → within ₹2,000 policy limit → AUTO_APPROVE → PayPal is executed.
    """
    # 1. Register agent with limit 2,000
    agent_res = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "ShoppingAgent",
            "type": "SHOPPING",
            "permissions": {
                "max_transaction_amount": "2000.00",
                "blocked_categories": ["gambling"],
            },
        },
    )
    assert agent_res.status_code == 201
    agent_id = agent_res.json()["id"]

    # 2. Set global policy max_auto_transaction = 2000.00
    policy_res = await async_client.post(
        "/api/v1/policies",
        json={
            "max_auto_transaction": "2000.00",
            "blocked_categories": ["gambling", "adult"],
        },
    )
    assert policy_res.status_code == 201

    # 3. Spend request of 1,000
    spend_payload = {
        "agent_id": agent_id,
        "amount": "1000.00",
        "currency": "USD",
        "merchant_name": "Walmart Supercenter",
        "claimed_category": "groceries",
        "description": "Weekly essentials",
    }

    initial_paypal_calls = mock_paypal_transport.order_creation_calls

    res = await async_client.post("/api/v1/spend", json=spend_payload)
    assert res.status_code == 200
    data = res.json()

    assert data["decision"] == "AUTO_APPROVE"
    assert data["paypal_order"] is not None
    assert data["paypal_order"]["order_id"].startswith("MOCK-ORD-")
    assert data["paypal_order"]["amount"] == "1000.00"

    # Verify PayPal WAS called
    assert mock_paypal_transport.order_creation_calls == initial_paypal_calls + 1

    # Verify PaymentRequest DB record
    stmt = select(PaymentRequest).where(PaymentRequest.id == data["payment_request_id"])
    req = (await db_session.execute(stmt)).scalar_one_or_none()
    assert req is not None
    assert req.decision == "AUTO_APPROVE"
    assert req.amount == Decimal("1000.00")


@pytest.mark.asyncio
async def test_milestone_2_block_5000_never_calls_paypal(
    async_client: httpx.AsyncClient,
    mock_paypal_transport: MockPayPalTransport,
    db_session: AsyncSession,
):
    """
    Milestone 2 Core Specification:
    ₹5,000 → exceeds ₹2,000 limit → BLOCK → PayPal must NOT be called.
    """
    # 1. Register agent with limit 2,000
    agent_res = await async_client.post(
        "/api/v1/agents",
        json={
            "name": "ShoppingAgent",
            "permissions": {"max_transaction_amount": "2000.00"},
        },
    )
    agent_id = agent_res.json()["id"]

    # 2. Set global policy max_auto_transaction = 2000.00
    await async_client.post(
        "/api/v1/policies",
        json={"max_auto_transaction": "2000.00"},
    )

    initial_paypal_calls = mock_paypal_transport.order_creation_calls

    # 3. Spend request of 5,000
    spend_payload = {
        "agent_id": agent_id,
        "amount": "5000.00",
        "currency": "USD",
        "merchant_name": "Apple Store",
        "claimed_category": "electronics",
        "description": "High-end laptop",
    }

    res = await async_client.post("/api/v1/spend", json=spend_payload)
    assert res.status_code == 200
    data = res.json()

    # Assert decision is BLOCK
    assert data["decision"] == "BLOCK"
    assert "TRANSACTION_LIMIT_EXCEEDED" in data["decision_reasons"]
    assert "POLICY_LIMIT_EXCEEDED" in data["decision_reasons"]
    assert data["paypal_order"] is None

    # CRITICAL LAW 1 ASSERTION: PayPal must NEVER be called for blocked transactions
    assert mock_paypal_transport.order_creation_calls == initial_paypal_calls

    # Verify PaymentRequest DB record shows BLOCKED
    stmt = select(PaymentRequest).where(PaymentRequest.id == data["payment_request_id"])
    req = (await db_session.execute(stmt)).scalar_one_or_none()
    assert req is not None
    assert req.decision == "BLOCK"
    assert "TRANSACTION_LIMIT_EXCEEDED" in req.decision_reasons


@pytest.mark.asyncio
async def test_blocked_category_never_calls_paypal(
    async_client: httpx.AsyncClient,
    mock_paypal_transport: MockPayPalTransport,
):
    """Verify transactions in blocked categories (e.g., gambling) are blocked without calling PayPal."""
    agent_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "GamingAgent", "permissions": {"max_transaction_amount": "1000.00"}},
    )
    agent_id = agent_res.json()["id"]

    await async_client.post(
        "/api/v1/policies",
        json={"max_auto_transaction": "2000.00", "blocked_categories": ["gambling"]},
    )

    initial_paypal_calls = mock_paypal_transport.order_creation_calls

    res = await async_client.post(
        "/api/v1/spend",
        json={
            "agent_id": agent_id,
            "amount": "250.00",
            "currency": "USD",
            "merchant_name": "Online Casino",
            "claimed_category": "gambling",
        },
    )
    data = res.json()
    assert data["decision"] == "BLOCK"
    assert "CATEGORY_BLOCKED" in data["decision_reasons"]
    assert data["paypal_order"] is None
    assert mock_paypal_transport.order_creation_calls == initial_paypal_calls


@pytest.mark.asyncio
async def test_revoked_agent_cannot_spend(
    async_client: httpx.AsyncClient,
    mock_paypal_transport: MockPayPalTransport,
):
    """Verify revoked agent requests are immediately blocked."""
    agent_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "CompromisedAgent"},
    )
    agent_id = agent_res.json()["id"]

    # Revoke agent
    revoke_res = await async_client.post(f"/api/v1/agents/{agent_id}/revoke")
    assert revoke_res.status_code == 200
    assert revoke_res.json()["status"] == "REVOKED"

    initial_paypal_calls = mock_paypal_transport.order_creation_calls

    res = await async_client.post(
        "/api/v1/spend",
        json={
            "agent_id": agent_id,
            "amount": "50.00",
            "currency": "USD",
            "merchant_name": "Bookstore",
            "claimed_category": "books",
        },
    )
    data = res.json()
    assert data["decision"] == "BLOCK"
    assert "AGENT_REVOKED" in data["decision_reasons"]
    assert mock_paypal_transport.order_creation_calls == initial_paypal_calls


@pytest.mark.asyncio
async def test_budget_exhaustion_blocks_spend(
    async_client: httpx.AsyncClient,
    mock_paypal_transport: MockPayPalTransport,
):
    """Verify spend exceeding monthly budget is blocked."""
    agent_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "TravelAgent", "permissions": {"max_transaction_amount": "5000.00"}},
    )
    agent_id = agent_res.json()["id"]

    # Set tight budget
    await async_client.post(
        "/api/v1/budgets",
        json={"monthly_limit": "1000.00", "protected_balance": "200.00"},  # available: 800
    )

    initial_paypal_calls = mock_paypal_transport.order_creation_calls

    # Attempt to spend 900 (exceeds available 800)
    res = await async_client.post(
        "/api/v1/spend",
        json={
            "agent_id": agent_id,
            "amount": "900.00",
            "currency": "USD",
            "merchant_name": "Airline Tickets",
            "claimed_category": "travel",
        },
    )
    data = res.json()
    assert data["decision"] == "BLOCK"
    assert "BUDGET_EXHAUSTED" in data["decision_reasons"]
    assert mock_paypal_transport.order_creation_calls == initial_paypal_calls
