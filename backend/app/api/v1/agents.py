import uuid
from datetime import datetime, timezone
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.agent import Agent, AgentPermission
from app.models.user import User
from app.schemas.agent import AgentCreate, AgentPermissionCreate, AgentResponse

router = APIRouter(prefix="/agents", tags=["Agents"])


async def _get_or_create_default_user(db: AsyncSession) -> User:
    """Helper to ensure a default user exists for development/demo."""
    stmt = select(User).limit(1)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            id=str(uuid.uuid4()),
            email="demo-user@guardian.ai",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new AI Agent",
)
async def create_agent(
    payload: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    user = await _get_or_create_default_user(db)
    agent = Agent(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name=payload.name,
        type=payload.type,
        status="ACTIVE",
    )
    db.add(agent)
    await db.flush()

    # Permissions
    perm_payload = payload.permissions or AgentPermissionCreate()
    permission = AgentPermission(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        max_transaction_amount=perm_payload.max_transaction_amount,
        daily_limit=perm_payload.daily_limit,
        monthly_limit=perm_payload.monthly_limit,
        allowed_categories=perm_payload.allowed_categories,
        blocked_categories=perm_payload.blocked_categories,
        starts_at=perm_payload.starts_at,
        expires_at=perm_payload.expires_at,
    )
    db.add(permission)
    await db.commit()

    # Refresh with relations
    stmt = (
        select(Agent)
        .options(selectinload(Agent.permissions))
        .where(Agent.id == agent.id)
    )
    res = await db.execute(stmt)
    refreshed_agent = res.scalar_one()
    return AgentResponse.model_validate(refreshed_agent)


@router.get(
    "",
    response_model=List[AgentResponse],
    summary="List registered AI agents",
)
async def list_agents(
    db: AsyncSession = Depends(get_db),
) -> List[AgentResponse]:
    stmt = select(Agent).options(selectinload(Agent.permissions)).order_by(Agent.created_at.desc())
    res = await db.execute(stmt)
    agents = res.scalars().all()
    return [AgentResponse.model_validate(a) for a in agents]


@router.get(
    "/{agent_id}",
    response_model=AgentResponse,
    summary="Get agent details and permissions",
)
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    stmt = select(Agent).options(selectinload(Agent.permissions)).where(Agent.id == agent_id)
    res = await db.execute(stmt)
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return AgentResponse.model_validate(agent)


@router.post(
    "/{agent_id}/revoke",
    response_model=AgentResponse,
    summary="Emergency revoke an AI agent",
)
async def revoke_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    stmt = select(Agent).options(selectinload(Agent.permissions)).where(Agent.id == agent_id)
    res = await db.execute(stmt)
    agent = res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    agent.status = "REVOKED"
    agent.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(agent)
    return AgentResponse.model_validate(agent)
