import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import _get_or_create_default_user
from app.core.database import get_db
from app.models.budget import Budget
from app.schemas.budget import BudgetCreate, BudgetResponse

router = APIRouter(prefix="/budgets", tags=["Budgets"])


@router.post(
    "",
    response_model=BudgetResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or update monthly budget constraints",
)
async def set_budget(
    payload: BudgetCreate,
    db: AsyncSession = Depends(get_db),
) -> BudgetResponse:
    user = await _get_or_create_default_user(db)
    stmt = select(Budget).where(Budget.user_id == user.id)
    budget = (await db.execute(stmt)).scalar_one_or_none()

    if budget:
        budget.monthly_limit = payload.monthly_limit
        budget.protected_balance = payload.protected_balance
        budget.currency = payload.currency
    else:
        budget = Budget(
            id=str(uuid.uuid4()),
            user_id=user.id,
            monthly_limit=payload.monthly_limit,
            protected_balance=payload.protected_balance,
            currency=payload.currency,
        )
        db.add(budget)

    await db.commit()
    await db.refresh(budget)
    return BudgetResponse(
        id=budget.id,
        user_id=budget.user_id,
        monthly_limit=budget.monthly_limit,
        current_spent=budget.current_spent,
        reserved_amount=budget.reserved_amount,
        protected_balance=budget.protected_balance,
        available_amount=budget.available_amount,
        currency=budget.currency,
        updated_at=budget.updated_at,
    )


@router.get(
    "",
    response_model=BudgetResponse,
    summary="Get current user budget and spending levels",
)
async def get_budget(
    db: AsyncSession = Depends(get_db),
) -> BudgetResponse:
    user = await _get_or_create_default_user(db)
    stmt = select(Budget).where(Budget.user_id == user.id)
    budget = (await db.execute(stmt)).scalar_one_or_none()

    if not budget:
        budget = Budget(
            id=str(uuid.uuid4()),
            user_id=user.id,
            monthly_limit=Decimal("20000.00"),
            protected_balance=Decimal("0.00"),
            currency="USD",
        )
        db.add(budget)
        await db.commit()
        await db.refresh(budget)

    return BudgetResponse(
        id=budget.id,
        user_id=budget.user_id,
        monthly_limit=budget.monthly_limit,
        current_spent=budget.current_spent,
        reserved_amount=budget.reserved_amount,
        protected_balance=budget.protected_balance,
        available_amount=budget.available_amount,
        currency=budget.currency,
        updated_at=budget.updated_at,
    )
