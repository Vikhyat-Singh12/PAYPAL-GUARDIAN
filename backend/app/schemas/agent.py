from decimal import Decimal
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentPermissionCreate(BaseModel):
    max_transaction_amount: Decimal = Field(
        default=Decimal("1000.00"),
        gt=Decimal("0.00"),
        description="Maximum single transaction limit for this agent.",
    )
    daily_limit: Optional[Decimal] = Field(default=None, gt=Decimal("0.00"))
    monthly_limit: Optional[Decimal] = Field(default=None, gt=Decimal("0.00"))
    allowed_categories: List[str] = Field(default_factory=list)
    blocked_categories: List[str] = Field(default_factory=list)
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    @field_validator("max_transaction_amount", "daily_limit", "monthly_limit")
    @classmethod
    def validate_decimals(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v.as_tuple().exponent < -2:
            raise ValueError("Limit amount cannot have more than 2 decimal places.")
        return v


class AgentPermissionResponse(BaseModel):
    id: str
    agent_id: str
    max_transaction_amount: Decimal
    daily_limit: Optional[Decimal] = None
    monthly_limit: Optional[Decimal] = None
    allowed_categories: List[str]
    blocked_categories: List[str]
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(default="GENERAL", max_length=50)
    permissions: Optional[AgentPermissionCreate] = None


class AgentResponse(BaseModel):
    id: str
    user_id: str
    name: str
    type: str
    status: str
    trust_score: int
    created_at: datetime
    revoked_at: Optional[datetime] = None
    permissions: Optional[AgentPermissionResponse] = None

    model_config = ConfigDict(from_attributes=True)
