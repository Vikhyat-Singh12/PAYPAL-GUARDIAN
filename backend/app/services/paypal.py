import time
from decimal import Decimal
from typing import Any, Dict, Optional
import httpx

from app.core.config import settings


class PayPalAPIError(Exception):
    """Custom exception raised when PayPal API returns an error."""

    def __init__(self, status_code: int, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(f"PayPal API Error [{status_code}]: {message}")
        self.status_code = status_code
        self.message = message
        self.details = details or {}


class PayPalService:
    """Service to interact with the PayPal Sandbox v2 REST API."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.client_id = settings.PAYPAL_CLIENT_ID if client_id is None else client_id
        self.client_secret = settings.PAYPAL_CLIENT_SECRET if client_secret is None else client_secret
        self.base_url = (settings.PAYPAL_BASE_URL if base_url is None else base_url).rstrip("/")
        self._http_client = http_client
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(timeout=30.0)

    async def get_access_token(self) -> str:
        """Obtain or return a cached OAuth 2.0 access token from PayPal."""
        now = time.time()
        if self._access_token and now < (self._token_expires_at - 60):
            return self._access_token

        if not self.client_id or not self.client_secret:
            raise PayPalAPIError(
                status_code=500,
                message="PayPal Client ID and Client Secret must be configured.",
            )

        token_url = f"{self.base_url}/v1/oauth2/token"
        headers = {
            "Accept": "application/json",
            "Accept-Language": "en_US",
        }
        data = {"grant_type": "client_credentials"}

        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.post(
                token_url,
                auth=(self.client_id, self.client_secret),
                headers=headers,
                data=data,
            )
        finally:
            if should_close:
                await client.aclose()

        if response.status_code != 200:
            try:
                err_body = response.json()
            except Exception:
                err_body = {"raw": response.text}
            raise PayPalAPIError(
                status_code=response.status_code,
                message="Failed to obtain PayPal OAuth token",
                details=err_body,
            )

        token_data = response.json()
        self._access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 3600)
        self._token_expires_at = now + float(expires_in)
        return self._access_token

    async def create_order(
        self,
        amount: Decimal,
        currency: str = "USD",
        reference_id: Optional[str] = None,
        custom_id: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a PayPal v2 order for immediate capture."""
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        formatted_amount = f"{amount:.2f}"

        purchase_unit: Dict[str, Any] = {
            "reference_id": reference_id or "default",
            "amount": {
                "currency_code": currency,
                "value": formatted_amount,
            },
        }

        if custom_id:
            purchase_unit["custom_id"] = custom_id
        if description:
            purchase_unit["description"] = description

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [purchase_unit],
        }

        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.post(url, headers=headers, json=payload)
        finally:
            if should_close:
                await client.aclose()

        if response.status_code not in (200, 201):
            try:
                err_body = response.json()
            except Exception:
                err_body = {"raw": response.text}
            raise PayPalAPIError(
                status_code=response.status_code,
                message="Failed to create PayPal order",
                details=err_body,
            )

        return response.json()

    async def capture_order(self, order_id: str) -> Dict[str, Any]:
        """Capture payment for an approved PayPal order."""
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{order_id}/capture"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.post(url, headers=headers, json={})
        finally:
            if should_close:
                await client.aclose()

        if response.status_code not in (200, 201):
            try:
                err_body = response.json()
            except Exception:
                err_body = {"raw": response.text}
            raise PayPalAPIError(
                status_code=response.status_code,
                message=f"Failed to capture PayPal order {order_id}",
                details=err_body,
            )

        return response.json()

    async def get_order_details(self, order_id: str) -> Dict[str, Any]:
        """Retrieve details of a PayPal order."""
        token = await self.get_access_token()
        url = f"{self.base_url}/v2/checkout/orders/{order_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        client = await self._get_client()
        should_close = self._http_client is None
        try:
            response = await client.get(url, headers=headers)
        finally:
            if should_close:
                await client.aclose()

        if response.status_code != 200:
            try:
                err_body = response.json()
            except Exception:
                err_body = {"raw": response.text}
            raise PayPalAPIError(
                status_code=response.status_code,
                message=f"Failed to get PayPal order {order_id}",
                details=err_body,
            )

        return response.json()


def get_paypal_service() -> PayPalService:
    return PayPalService()
