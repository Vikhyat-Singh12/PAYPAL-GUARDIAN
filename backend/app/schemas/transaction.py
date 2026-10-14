from decimal import Decimal
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateOrderRequest(BaseModel):
    # Law 6: Money Uses Decimal, Never Float
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.00"),
        description="Amount to charge. Must be strictly positive and formatted as Decimal.",
    )
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="3-letter ISO 4217 currency code (e.g., USD, EUR, INR).",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional human-readable description of the purchase.",
    )
    merchant_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Name of the merchant or store.",
    )

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("Amount cannot have more than 2 decimal places.")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return v.strip().upper()


class CaptureOrderRequest(BaseModel):
    order_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="PayPal Order ID to be captured.",
    )


class TransactionResponse(BaseModel):
    id: str
    payment_request_id: Optional[str] = None
    paypal_order_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    amount: Decimal
    currency: str
    status: str
    merchant_name: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PayPalOrderResponse(BaseModel):
    order_id: str
    status: str
    approval_url: Optional[str] = None
    transaction_id: str
    amount: Decimal
    currency: str


class PayPalCaptureResponse(BaseModel):
    capture_id: str
    order_id: str
    status: str
    transaction_id: str
    amount: Decimal
    currency: str
