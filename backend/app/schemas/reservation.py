from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReservationCreate(BaseModel):
    # Law 6: Decimal, never float
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.00"),
        description="Amount to reserve. Must be positive decimal with at most 2 decimal places.",
    )
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="ISO 4217 Currency Code",
    )
    agent_id: Optional[str] = Field(
        default=None,
        description="Optional agent ID requesting the reservation",
    )
    payment_request_id: Optional[str] = Field(
        default=None,
        description="Optional payment request ID linked to this reservation",
    )
    ttl_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="Time to live in seconds before automatic expiry (default: 300s = 5m)",
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


class ReservationResponse(BaseModel):
    id: str
    budget_id: str
    agent_id: Optional[str] = None
    payment_request_id: Optional[str] = None
    amount: Decimal
    currency: str
    status: str
    expires_at: datetime
    created_at: datetime
    settled_at: Optional[datetime] = None
    released_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ReservationSweepResponse(BaseModel):
    expired_count: int
    released_amount: Decimal
    message: str
