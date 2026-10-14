from decimal import Decimal
import httpx
import pytest

from app.services.paypal import PayPalAPIError, PayPalService


@pytest.mark.asyncio
async def test_oauth_token_retrieval_and_caching():
    """Verify OAuth token is acquired and cached properly."""
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if request.url.path == "/v1/oauth2/token":
            call_count += 1
            return httpx.Response(
                200,
                json={
                    "access_token": "test-access-token-xyz",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = PayPalService(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api-m.sandbox.paypal.com",
        http_client=client,
    )

    token1 = await service.get_access_token()
    assert token1 == "test-access-token-xyz"
    assert call_count == 1

    token2 = await service.get_access_token()
    assert token2 == "test-access-token-xyz"
    assert call_count == 1


@pytest.mark.asyncio
async def test_oauth_missing_credentials():
    """Verify error is raised if credentials are empty."""
    service = PayPalService(client_id="", client_secret="")
    with pytest.raises(PayPalAPIError) as exc_info:
        await service.get_access_token()
    assert exc_info.value.status_code == 500
    assert "must be configured" in exc_info.value.message


@pytest.mark.asyncio
async def test_create_order_payload_and_decimal_precision():
    """Verify create_order correctly formats Decimal amounts (Law 6) and PayPal schema."""
    captured_body = {}

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        if request.url.path == "/v2/checkout/orders":
            import json
            captured_body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(201, json={"id": "ORDER-999", "status": "CREATED"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = PayPalService(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api-m.sandbox.paypal.com",
        http_client=client,
    )

    amount = Decimal("250.50")
    order = await service.create_order(
        amount=amount,
        currency="USD",
        custom_id="guardian-tx-001",
        description="Autonomous Agent Grocery Purchase",
    )

    assert order["id"] == "ORDER-999"
    assert order["status"] == "CREATED"
    assert captured_body["intent"] == "CAPTURE"
    assert captured_body["purchase_units"][0]["amount"]["value"] == "250.50"
    assert captured_body["purchase_units"][0]["amount"]["currency_code"] == "USD"
    assert captured_body["purchase_units"][0]["custom_id"] == "guardian-tx-001"
    assert captured_body["purchase_units"][0]["description"] == "Autonomous Agent Grocery Purchase"


@pytest.mark.asyncio
async def test_capture_order():
    """Verify capture_order calls the correct endpoint and extracts response."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        if request.url.path == "/v2/checkout/orders/ORDER-999/capture":
            return httpx.Response(
                201,
                json={
                    "id": "ORDER-999",
                    "status": "COMPLETED",
                    "purchase_units": [
                        {
                            "payments": {
                                "captures": [
                                    {
                                        "id": "CAPTURE-777",
                                        "status": "COMPLETED",
                                        "amount": {"currency_code": "USD", "value": "250.50"},
                                    }
                                ]
                            }
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = PayPalService(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api-m.sandbox.paypal.com",
        http_client=client,
    )

    capture_res = await service.capture_order("ORDER-999")
    assert capture_res["status"] == "COMPLETED"
    assert capture_res["purchase_units"][0]["payments"]["captures"][0]["id"] == "CAPTURE-777"


@pytest.mark.asyncio
async def test_paypal_api_error_propagation():
    """Verify PayPal 422 error is wrapped as PayPalAPIError."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "token-123", "expires_in": 3600})
        if request.url.path == "/v2/checkout/orders":
            return httpx.Response(
                422,
                json={
                    "name": "UNPROCESSABLE_ENTITY",
                    "message": "The requested action could not be performed.",
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    service = PayPalService(
        client_id="test_id",
        client_secret="test_secret",
        base_url="https://api-m.sandbox.paypal.com",
        http_client=client,
    )

    with pytest.raises(PayPalAPIError) as exc_info:
        await service.create_order(amount=Decimal("10.00"), currency="USD")

    assert exc_info.value.status_code == 422
    assert "UNPROCESSABLE_ENTITY" in str(exc_info.value.details)
