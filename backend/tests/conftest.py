import asyncio
from decimal import Decimal
from typing import AsyncGenerator, List
import uuid
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.services.paypal import PayPalService, get_paypal_service


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Ensure all database tables exist for the test session."""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield

    cleanup_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with cleanup_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await cleanup_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_database_between_tests():
    """Clean all database tables between each test execution for isolation."""
    yield
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE reservations, payment_requests, transactions, agent_permissions, "
                "agents, budgets, policies, users CASCADE;"
            )
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an isolated database session for querying test state."""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    SessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with SessionLocal() as session:
        yield session

    await engine.dispose()


class MockPayPalTransport(httpx.AsyncBaseTransport):
    """Mock HTTP transport simulating PayPal Sandbox v2 REST responses with call tracking."""

    def __init__(self):
        self.orders = {}
        self.captures = {}
        self.order_creation_calls = 0
        self.capture_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url_path = request.url.path

        # 1. OAuth2 Token endpoint
        if url_path == "/v1/oauth2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "mock-sandbox-access-token-12345",
                    "token_type": "Bearer",
                    "app_id": "APP-80W284485P519543T",
                    "expires_in": 32400,
                },
            )

        # 2. Create Order endpoint: /v2/checkout/orders
        if url_path == "/v2/checkout/orders" and request.method == "POST":
            self.order_creation_calls += 1
            import json
            body = json.loads(request.content.decode("utf-8"))
            order_id = f"MOCK-ORD-{uuid.uuid4().hex[:10].upper()}"
            amount_info = body["purchase_units"][0]["amount"]
            order_obj = {
                "id": order_id,
                "status": "CREATED",
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": body["purchase_units"][0].get("reference_id", "default"),
                        "amount": amount_info,
                    }
                ],
                "links": [
                    {
                        "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                        "rel": "approve",
                        "method": "GET",
                    }
                ],
            }
            self.orders[order_id] = order_obj
            return httpx.Response(201, json=order_obj)

        # 3. Capture Order endpoint: /v2/checkout/orders/{id}/capture
        if "/capture" in url_path and request.method == "POST":
            self.capture_calls += 1
            parts = url_path.strip("/").split("/")
            order_id = parts[3]
            capture_id = f"MOCK-CAP-{order_id}"
            capture_obj = {
                "id": order_id,
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [
                                {
                                    "id": capture_id,
                                    "status": "COMPLETED",
                                    "amount": {
                                        "currency_code": "USD",
                                        "value": "100.00",
                                    },
                                }
                            ]
                        }
                    }
                ],
            }
            self.captures[order_id] = capture_obj
            return httpx.Response(201, json=capture_obj)

        # 4. Get Order endpoint: /v2/checkout/orders/{id}
        if url_path.startswith("/v2/checkout/orders/") and request.method == "GET":
            order_id = url_path.split("/")[-1]
            if order_id in self.orders:
                return httpx.Response(200, json=self.orders[order_id])
            return httpx.Response(404, json={"message": "Order not found"})

        return httpx.Response(404, json={"message": "Route not found in mock transport"})


@pytest.fixture
def mock_paypal_transport() -> MockPayPalTransport:
    return MockPayPalTransport()


@pytest.fixture
def mock_paypal_service(mock_paypal_transport: MockPayPalTransport) -> PayPalService:
    mock_client = httpx.AsyncClient(transport=mock_paypal_transport, base_url="https://api-m.sandbox.paypal.com")
    return PayPalService(
        client_id="mock_client_id",
        client_secret="mock_client_secret",
        base_url="https://api-m.sandbox.paypal.com",
        http_client=mock_client,
    )


@pytest_asyncio.fixture
async def async_client(mock_paypal_service: PayPalService) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Test client with NullPool DB override and Mock PayPal service."""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    SessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_paypal_service] = lambda: mock_paypal_service

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()
