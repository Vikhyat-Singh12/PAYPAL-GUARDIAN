from decimal import Decimal
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PolicyCreate(BaseModel):
    max_auto_transaction: Decimal = Field(
        default=Decimal("2000.00"),
        gt=Decimal("0.00"),
        description="Global ceiling for automated payment authorization.",
    )
    blocked_categories: List[str] = Field(
        default_factory=lambda: ["gambling", "adult", "weapons"],
        description="Categories permanently prohibited under this policy.",
    )
    category_limits: Dict[str, Decimal] = Field(
        default_factory=dict,
        description="Per-category ceilings (e.g. {'groceries': 3000.00}).",
    )

    @field_validator("max_auto_transaction")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("Amount cannot have more than 2 decimal places.")
        return v


class PolicyResponse(BaseModel):
    id: str
    user_id: str
    version: int
    max_auto_transaction: Decimal
    blocked_categories: List[str]
    category_limits: Dict[str, Any]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
