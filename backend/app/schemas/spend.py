from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from app.schemas.transaction import PayPalOrderResponse


class SpendRequest(BaseModel):
    # Zero-Trust Agent Input (Law 2)
    agent_id: str = Field(..., description="ID of the autonomous agent initiating spend")
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.00"),
        description="Amount to spend. Must be positive Decimal.",
    )
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="ISO 4217 Currency Code",
    )
    merchant_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Target merchant name",
    )
    claimed_category: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Category claimed by agent (e.g. groceries, travel, electronics)",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional intent reason or summary",
    )

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("Amount cannot have more than 2 decimal places.")
        return v

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return v.strip().upper()


class SpendResponse(BaseModel):
    payment_request_id: str
    decision: str  # AUTO_APPROVE, BLOCK
    decision_reasons: List[str] = Field(default_factory=list)
    policy_version: Optional[int] = None
    paypal_order: Optional[PayPalOrderResponse] = None
    message: str
