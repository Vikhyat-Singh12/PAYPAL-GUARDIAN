from typing import List, Optional
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import _get_or_create_default_user
from app.core.database import get_db
from app.models.budget import Budget
from app.models.reservation import Reservation
from app.schemas.reservation import (
    ReservationCreate,
    ReservationResponse,
    ReservationSweepResponse,
)
from app.services.reservation import (
    InsufficientFundsError,
    InvalidReservationStateError,
    ReservationNotFoundError,
    ReservationService,
)

router = APIRouter(prefix="/reservations", tags=["Atomic Reservations"])


@router.post(
    "",
    response_model=ReservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Atomically reserve budget with PostgreSQL row-level locking",
)
async def create_reservation(
    payload: ReservationCreate,
    db: AsyncSession = Depends(get_db),
) -> ReservationResponse:
    user = await _get_or_create_default_user(db)
    budget_stmt = select(Budget).where(Budget.user_id == user.id)
    budget = (await db.execute(budget_stmt)).scalar_one_or_none()

    if not budget:
        # Default budget if none initialized yet
        budget = Budget(
            user_id=user.id,
            monthly_limit=Decimal("20000.00"),
            currency=payload.currency,
        )
        db.add(budget)
        await db.commit()
        await db.refresh(budget)

    try:
        reservation = await ReservationService.reserve(
            db=db,
            budget_id=budget.id,
            amount=payload.amount,
            currency=payload.currency,
            agent_id=payload.agent_id,
            payment_request_id=payload.payment_request_id,
            ttl_seconds=payload.ttl_seconds,
        )
        return ReservationResponse.model_validate(reservation)
    except InsufficientFundsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except ReservationNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get(
    "",
    response_model=List[ReservationResponse],
    summary="List budget reservations",
)
async def list_reservations(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> List[ReservationResponse]:
    stmt = select(Reservation).order_by(Reservation.created_at.desc())
    if status_filter:
        stmt = stmt.where(Reservation.status == status_filter.upper())
    result = await db.execute(stmt)
    reservations = result.scalars().all()
    return [ReservationResponse.model_validate(r) for r in reservations]


@router.get(
    "/{reservation_id}",
    response_model=ReservationResponse,
    summary="Get reservation details",
)
async def get_reservation(
    reservation_id: str,
    db: AsyncSession = Depends(get_db),
) -> ReservationResponse:
    stmt = select(Reservation).where(Reservation.id == reservation_id)
    result = await db.execute(stmt)
    res = result.scalar_one_or_none()
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found.")
    return ReservationResponse.model_validate(res)


@router.post(
    "/{reservation_id}/settle",
    response_model=ReservationResponse,
    summary="Settle reservation (moves reserved funds to spent)",
)
async def settle_reservation(
    reservation_id: str,
    db: AsyncSession = Depends(get_db),
) -> ReservationResponse:
    try:
        settled = await ReservationService.settle(db, reservation_id)
        return ReservationResponse.model_validate(settled)
    except ReservationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found.")
    except InvalidReservationStateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post(
    "/{reservation_id}/release",
    response_model=ReservationResponse,
    summary="Release reservation (returns reserved funds to available balance)",
)
async def release_reservation(
    reservation_id: str,
    db: AsyncSession = Depends(get_db),
) -> ReservationResponse:
    try:
        released = await ReservationService.release(db, reservation_id)
        return ReservationResponse.model_validate(released)
    except ReservationNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found.")
    except InvalidReservationStateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post(
    "/expire/sweep",
    response_model=ReservationSweepResponse,
    summary="Sweep and expire all overdue reservations",
)
async def sweep_expired_reservations(
    db: AsyncSession = Depends(get_db),
) -> ReservationSweepResponse:
    expired = await ReservationService.expire_overdue(db)
    total_released = sum((r.amount for r in expired), Decimal("0.00"))
    return ReservationSweepResponse(
        expired_count=len(expired),
        released_amount=total_released,
        message=f"Expired {len(expired)} overdue reservation(s) and released {total_released} back to available budget.",
    )
