from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional
import uuid
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.reservation import Reservation, ReservationStatus


class InsufficientFundsError(Exception):
    """Raised when available budget is insufficient for requested reservation."""
    pass


class ReservationNotFoundError(Exception):
    """Raised when reservation ID does not exist."""
    pass


class InvalidReservationStateError(Exception):
    """Raised when an operation cannot be performed on reservation in current status."""
    pass


class ReservationService:
    """
    Milestone 3 — Atomic Reservation Service.
    Implements PostgreSQL row-level locking (SELECT ... FOR UPDATE) to ensure
    atomic budget holds, preventing race conditions and double-spending across concurrent agents.
    """

    @staticmethod
    async def reserve(
        db: AsyncSession,
        budget_id: str,
        amount: Decimal,
        currency: str = "USD",
        agent_id: Optional[str] = None,
        payment_request_id: Optional[str] = None,
        ttl_seconds: int = 300,
    ) -> Reservation:
        """
        Atomically reserve funds against a budget using PostgreSQL row-level locking.
        Acquires row-level lock on the Budget record before checking available balance.
        """
        now = datetime.now(timezone.utc)

        # 1. Row-level lock on Budget (SELECT ... FOR UPDATE)
        budget_stmt = (
            select(Budget)
            .where(Budget.id == budget_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        budget_res = await db.execute(budget_stmt)
        budget = budget_res.scalar_one_or_none()

        if not budget:
            raise ReservationNotFoundError(f"Budget with id {budget_id} not found.")

        # 2. Lazy sweep: Expire any overdue pending reservations for this budget to free up held funds
        expired_stmt = (
            select(Reservation)
            .where(
                and_(
                    Reservation.budget_id == budget_id,
                    Reservation.status == ReservationStatus.PENDING.value,
                    Reservation.expires_at <= now,
                )
            )
            .with_for_update()
        )
        expired_reservations = (await db.execute(expired_stmt)).scalars().all()
        for exp_res in expired_reservations:
            exp_res.status = ReservationStatus.EXPIRED.value
            budget.reserved_amount = max(Decimal("0.00"), budget.reserved_amount - exp_res.amount)

        # 3. Check available balance under lock (TOCTOU protection)
        available = budget.available_amount
        if amount > available:
            # Insufficient funds: fail atomically without modifying state
            raise InsufficientFundsError(
                f"Insufficient funds: available {available} {currency}, requested {amount} {currency}."
            )

        # 4. Atomic deduction to reserved_amount and insert reservation
        budget.reserved_amount += amount
        expires_at = now + timedelta(seconds=ttl_seconds)

        reservation = Reservation(
            id=str(uuid.uuid4()),
            budget_id=budget.id,
            agent_id=agent_id,
            payment_request_id=payment_request_id,
            amount=amount,
            currency=currency,
            status=ReservationStatus.PENDING.value,
            expires_at=expires_at,
        )
        db.add(reservation)
        await db.commit()
        await db.refresh(reservation)
        return reservation

    @staticmethod
    async def settle(
        db: AsyncSession,
        reservation_id: str,
    ) -> Reservation:
        """
        Atomically settle a pending reservation upon confirmed payment execution.
        Moves funds from reserved_amount into current_spent under row-level lock.
        """
        # Lock reservation
        res_stmt = (
            select(Reservation)
            .where(Reservation.id == reservation_id)
            .with_for_update()
        )
        res = (await db.execute(res_stmt)).scalar_one_or_none()

        if not res:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found.")

        if res.status != ReservationStatus.PENDING.value:
            raise InvalidReservationStateError(
                f"Cannot settle reservation {reservation_id} with status '{res.status}'."
            )

        # Lock budget
        budget_stmt = (
            select(Budget)
            .where(Budget.id == res.budget_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        budget = (await db.execute(budget_stmt)).scalar_one()

        # Atomic shift: reserved -> spent
        budget.reserved_amount = max(Decimal("0.00"), budget.reserved_amount - res.amount)
        budget.current_spent += res.amount

        res.status = ReservationStatus.SETTLED.value
        res.settled_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(res)
        return res

    @staticmethod
    async def release(
        db: AsyncSession,
        reservation_id: str,
    ) -> Reservation:
        """
        Atomically release a pending reservation upon payment rejection, abort, or failure.
        Decrements reserved_amount, returning funds to available balance under row-level lock.
        """
        # Lock reservation
        res_stmt = (
            select(Reservation)
            .where(Reservation.id == reservation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        res = (await db.execute(res_stmt)).scalar_one_or_none()

        if not res:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found.")

        if res.status != ReservationStatus.PENDING.value:
            raise InvalidReservationStateError(
                f"Cannot release reservation {reservation_id} with status '{res.status}'."
            )

        # Lock budget
        budget_stmt = (
            select(Budget)
            .where(Budget.id == res.budget_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        budget = (await db.execute(budget_stmt)).scalar_one()

        # Atomic restoration of available balance
        budget.reserved_amount = max(Decimal("0.00"), budget.reserved_amount - res.amount)

        res.status = ReservationStatus.RELEASED.value
        res.released_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(res)
        return res

    @staticmethod
    async def expire_overdue(
        db: AsyncSession,
        budget_id: Optional[str] = None,
    ) -> List[Reservation]:
        """
        Find and expire all overdue pending reservations whose TTL has elapsed.
        Releases reserved funds back into available budget.
        """
        now = datetime.now(timezone.utc)
        conditions = [
            Reservation.status == ReservationStatus.PENDING.value,
            Reservation.expires_at <= now,
        ]
        if budget_id:
            conditions.append(Reservation.budget_id == budget_id)

        # Select overdue reservations with lock
        stmt = (
            select(Reservation)
            .where(and_(*conditions))
            .with_for_update()
        )
        overdue_reservations = (await db.execute(stmt)).scalars().all()

        if not overdue_reservations:
            return []

        # Group by budget to update reserved_amount
        budget_ids = {r.budget_id for r in overdue_reservations}
        for b_id in budget_ids:
            b_stmt = select(Budget).where(Budget.id == b_id).with_for_update()
            budget = (await db.execute(b_stmt)).scalar_one_or_none()
            if not budget:
                continue

            released_for_budget = sum(
                r.amount for r in overdue_reservations if r.budget_id == b_id
            )
            budget.reserved_amount = max(Decimal("0.00"), budget.reserved_amount - released_for_budget)

        for r in overdue_reservations:
            r.status = ReservationStatus.EXPIRED.value

        await db.commit()
        for r in overdue_reservations:
            await db.refresh(r)

        return overdue_reservations
