import uuid
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.agents import _get_or_create_default_user
from app.core.database import get_db
from app.models.policy import Policy
from app.schemas.policy import PolicyCreate, PolicyResponse

router = APIRouter(prefix="/policies", tags=["Policies"])


@router.post(
    "",
    response_model=PolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or update deterministic policy",
)
async def create_policy(
    payload: PolicyCreate,
    db: AsyncSession = Depends(get_db),
) -> PolicyResponse:
    user = await _get_or_create_default_user(db)

    # Deactivate previous active policy for user
    await db.execute(
        update(Policy)
        .where(Policy.user_id == user.id, Policy.is_active == True)
        .values(is_active=False)
    )

    # Get latest version number
    version_stmt = (
        select(Policy.version)
        .where(Policy.user_id == user.id)
        .order_by(Policy.version.desc())
        .limit(1)
    )
    latest_version = (await db.execute(version_stmt)).scalar_one_or_none() or 0

    new_policy = Policy(
        id=str(uuid.uuid4()),
        user_id=user.id,
        version=latest_version + 1,
        max_auto_transaction=payload.max_auto_transaction,
        blocked_categories=payload.blocked_categories,
        category_limits={k: str(v) for k, v in payload.category_limits.items()},
        is_active=True,
    )
    db.add(new_policy)
    await db.commit()
    await db.refresh(new_policy)
    return PolicyResponse.model_validate(new_policy)


@router.get(
    "/active",
    response_model=PolicyResponse,
    summary="Get current active policy",
)
async def get_active_policy(
    db: AsyncSession = Depends(get_db),
) -> PolicyResponse:
    user = await _get_or_create_default_user(db)
    stmt = (
        select(Policy)
        .where(Policy.user_id == user.id, Policy.is_active == True)
        .order_by(Policy.version.desc())
    )
    policy = (await db.execute(stmt)).scalars().first()

    if not policy:
        # Create default initial policy
        policy = Policy(
            id=str(uuid.uuid4()),
            user_id=user.id,
            version=1,
            is_active=True,
        )
        db.add(policy)
        await db.commit()
        await db.refresh(policy)

    return PolicyResponse.model_validate(policy)
