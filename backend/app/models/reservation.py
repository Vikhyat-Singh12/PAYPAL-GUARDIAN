import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ReservationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class Reservation(Base):
    """
    Milestone 3 — Atomic Reservation.
    Holds an atomic lock/reservation on a portion of the user's budget
    preventing double-spend / TOCTOU race conditions during multi-agent commerce.
    """
    __tablename__ = "reservations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    budget_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("budgets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    payment_request_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("payment_requests.id", ondelete="SET NULL"),
        nullable=True,
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
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ReservationStatus.PENDING.value,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    settled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    budget = relationship("Budget", back_populates="reservations")
    agent = relationship("Agent")
    payment_request = relationship("PaymentRequest")

    __table_args__ = (
        Index("ix_reservations_status_expires_at", "status", "expires_at"),
    )

    @property
    def is_active(self) -> bool:
        return self.status == ReservationStatus.PENDING.value and self.expires_at > datetime.now(timezone.utc)
