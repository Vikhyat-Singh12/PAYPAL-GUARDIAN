from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BudgetCreate(BaseModel):
    monthly_limit: Decimal = Field(
        default=Decimal("20000.00"),
        gt=Decimal("0.00"),
        description="Global monthly budget for all user agents.",
    )
    protected_balance: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        description="Untouchable reserve floor.",
    )
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("monthly_limit", "protected_balance")
    @classmethod
    def validate_amounts(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("Amount cannot have more than 2 decimal places.")
        return v

    @field_validator("currency")
    @classmethod
    def validate_curr(cls, v: str) -> str:
        return v.strip().upper()


class BudgetResponse(BaseModel):
    id: str
    user_id: str
    monthly_limit: Decimal
    current_spent: Decimal
    reserved_amount: Decimal
    protected_balance: Decimal
    available_amount: Decimal
    currency: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
