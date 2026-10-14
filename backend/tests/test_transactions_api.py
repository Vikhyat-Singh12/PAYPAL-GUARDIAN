from decimal import Decimal
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.transaction import Transaction


@pytest.mark.asyncio
async def test_health_check(async_client: httpx.AsyncClient):
    """Verify health endpoint."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "milestone" in data


@pytest.mark.asyncio
async def test_create_and_capture_order_flow(async_client: httpx.AsyncClient, db_session: AsyncSession):
    """
    Test End-to-End Milestone 1 flow:
    1. Guardian receives spend request
    2. Persists local transaction
    3. Creates PayPal Sandbox order
    4. Captures PayPal order
    5. Persists capture ID and updates transaction state
    """
    # 1. Create order
    create_payload = {
        "amount": "149.99",
        "currency": "USD",
        "description": "Agent Travel Booking",
        "merchant_name": "Expedia Partner",
    }
    create_res = await async_client.post("/api/v1/paypal/orders", json=create_payload)
    assert create_res.status_code == 201
    create_data = create_res.json()
    assert create_data["order_id"].startswith("MOCK-ORD-")
    assert create_data["status"] == "CREATED"
    assert create_data["amount"] == "149.99"
    assert create_data["currency"] == "USD"
    tx_id = create_data["transaction_id"]
    order_id = create_data["order_id"]

    # Verify DB state after creation
    stmt = select(Transaction).where(Transaction.id == tx_id)
    tx = (await db_session.execute(stmt)).scalar_one_or_none()
    assert tx is not None
    assert tx.paypal_order_id == order_id
    assert tx.status == "CREATED"
    assert tx.amount == Decimal("149.99")

    # 2. Capture order
    capture_res = await async_client.post(f"/api/v1/paypal/orders/{order_id}/capture")
    assert capture_res.status_code == 200
    capture_data = capture_res.json()
    assert capture_data["capture_id"] == f"MOCK-CAP-{order_id}"
    assert capture_data["status"] == "CAPTURED"
    assert capture_data["order_id"] == order_id

    # Verify DB state after capture
    await db_session.refresh(tx)
    assert tx.status == "CAPTURED"
    assert tx.paypal_capture_id == f"MOCK-CAP-{order_id}"


@pytest.mark.asyncio
async def test_input_validation_law_6(async_client: httpx.AsyncClient):
    """Verify Law 6: strict validation of monetary amounts."""
    # Negative amount
    res = await async_client.post(
        "/api/v1/paypal/orders",
        json={"amount": "-50.00", "currency": "USD"},
    )
    assert res.status_code == 422

    # Zero amount
    res = await async_client.post(
        "/api/v1/paypal/orders",
        json={"amount": "0.00", "currency": "USD"},
    )
    assert res.status_code == 422

    # More than 2 decimal places
    res = await async_client.post(
        "/api/v1/paypal/orders",
        json={"amount": "10.999", "currency": "USD"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_capture_nonexistent_order_returns_404(async_client: httpx.AsyncClient):
    """Verify capture of unknown order ID returns 404."""
    res = await async_client.post("/api/v1/paypal/orders/NON-EXISTENT-ORDER/capture")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_and_get_transaction(async_client: httpx.AsyncClient):
    """Verify querying transactions list and single transaction."""
    # Create a transaction
    create_res = await async_client.post(
        "/api/v1/paypal/orders",
        json={"amount": "45.00", "currency": "USD", "merchant_name": "Test Store"},
    )
    tx_id = create_res.json()["transaction_id"]

    # List
    list_res = await async_client.get("/api/v1/paypal/transactions")
    assert list_res.status_code == 200
    tx_list = list_res.json()
    assert isinstance(tx_list, list)
    assert any(t["id"] == tx_id for t in tx_list)

    # Get single
    get_res = await async_client.get(f"/api/v1/paypal/transactions/{tx_id}")
    assert get_res.status_code == 200
    single_tx = get_res.json()
    assert single_tx["id"] == tx_id
    assert single_tx["amount"] == "45.00"
    assert single_tx["merchant_name"] == "Test Store"


@pytest.mark.asyncio
async def test_law_3_secrets_never_leaked(async_client: httpx.AsyncClient):
    """Verify Law 3: responses never expose internal credentials."""
    res = await async_client.post(
        "/api/v1/paypal/orders",
        json={"amount": "15.00", "currency": "USD"},
    )
    text = res.text.lower()
    assert "secret" not in text
    assert "access_token" not in text


@pytest.mark.asyncio
async def test_repeated_capture_is_idempotent(async_client: httpx.AsyncClient):
    """Verify that calling capture on an already-captured order returns existing capture data idempotently."""
    create_res = await async_client.post(
        "/api/v1/paypal/orders",
        json={"amount": "55.00", "currency": "USD"},
    )
    order_id = create_res.json()["order_id"]

    # First capture
    cap1 = await async_client.post(f"/api/v1/paypal/orders/{order_id}/capture")
    assert cap1.status_code == 200
    cap1_data = cap1.json()
    assert cap1_data["status"] == "CAPTURED"

    # Second capture - idempotent
    cap2 = await async_client.post(f"/api/v1/paypal/orders/{order_id}/capture")
    assert cap2.status_code == 200
    cap2_data = cap2.json()
    assert cap2_data["capture_id"] == cap1_data["capture_id"]
    assert cap2_data["status"] == "CAPTURED"


@pytest.mark.asyncio
async def test_create_order_paypal_error_marks_transaction_failed(
    async_client: httpx.AsyncClient, db_session: AsyncSession
):
    """Verify that if PayPal rejects order creation, the transaction is marked as FAILED in DB."""
    # We provide a mock service that fails on create_order
    from app.services.paypal import PayPalAPIError, PayPalService, get_paypal_service

    class FailingPayPalService(PayPalService):
        async def create_order(self, *args, **kwargs):
            raise PayPalAPIError(status_code=422, message="Currency not supported")

    app.dependency_overrides[get_paypal_service] = lambda: FailingPayPalService()

    res = await async_client.post(
        "/api/v1/paypal/orders",
        json={"amount": "100.00", "currency": "XYZ"},
    )
    assert res.status_code == 422
    assert "Currency not supported" in res.json()["detail"]["message"]

    # Verify that a failed transaction was stored in DB
    stmt = select(Transaction).where(Transaction.status == "FAILED")
    tx = (await db_session.execute(stmt)).scalar_one_or_none()
    assert tx is not None
    assert tx.status == "FAILED"
    assert tx.amount == Decimal("100.00")

