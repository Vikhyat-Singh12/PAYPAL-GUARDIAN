import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Law 6: Decimal, never float
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )
    merchant_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    # Law 2: Zero-Trust Agent Payloads - category declared by agent is untrusted
    claimed_category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    # Decision: AUTO_APPROVE, BLOCK
    decision: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    # Structured reason codes
    decision_reasons: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    policy_version: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    agent = relationship("Agent", back_populates="payment_requests")
    transaction = relationship("Transaction", back_populates="payment_request", uselist=False)
