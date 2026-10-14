import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    payment_request_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("payment_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    paypal_order_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    paypal_capture_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    # Law 6: Money Uses Decimal, Never Float (NUMERIC(12, 2))
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PENDING",
        index=True,
    )
    merchant_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    payment_request = relationship("PaymentRequest", back_populates="transaction")

