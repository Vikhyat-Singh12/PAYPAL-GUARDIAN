import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # Law 6: Decimal, never float
    monthly_limit: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("20000.00"),
    )
    current_spent: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    reserved_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    protected_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user = relationship("User", back_populates="budget")
    reservations = relationship("Reservation", back_populates="budget", cascade="all, delete-orphan")

    @property
    def available_amount(self) -> Decimal:
        """Calculate unreserved, unprotected budget available for spending."""
        protected = self.protected_balance if self.protected_balance is not None else Decimal("0.00")
        spent = self.current_spent if self.current_spent is not None else Decimal("0.00")
        reserved = self.reserved_amount if self.reserved_amount is not None else Decimal("0.00")
        limit = self.monthly_limit if self.monthly_limit is not None else Decimal("0.00")

        spendable_ceiling = limit - protected
        spent_and_reserved = spent + reserved
        return max(Decimal("0.00"), spendable_ceiling - spent_and_reserved)

