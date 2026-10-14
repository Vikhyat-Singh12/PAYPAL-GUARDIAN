import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.engine.policy_engine import DecisionEnum, PolicyDecision, PolicyEngine
from app.models.agent import Agent
from app.models.budget import Budget
from app.models.payment_request import PaymentRequest
from app.models.policy import Policy
from app.models.transaction import Transaction
from app.schemas.spend import SpendRequest, SpendResponse
from app.schemas.transaction import PayPalOrderResponse
from app.services.paypal import PayPalAPIError, PayPalService, get_paypal_service
from app.services.reservation import InsufficientFundsError, ReservationService

router = APIRouter(prefix="/spend", tags=["Spend Authorization"])


def _extract_approval_url(order_data: dict) -> Optional[str]:
    links = order_data.get("links", [])
    for link in links:
        if link.get("rel") in ("payer-action", "approve"):
            return link.get("href")
    return None


@router.post(
    "",
    response_model=SpendResponse,
    status_code=status.HTTP_200_OK,
    summary="Authorize spend request via Guardian Policy Engine",
    description=(
        "Evaluates an autonomous agent's spend request against deterministic Guardian policies, "
        "agent permissions, and basic budget constraints. If approved, executes PayPal order; "
        "if blocked, PayPal is NEVER called."
    ),
)
async def request_spend(
    payload: SpendRequest,
    db: AsyncSession = Depends(get_db),
    paypal_service: PayPalService = Depends(get_paypal_service),
) -> SpendResponse:
    # 1. Fetch Agent with permissions
    stmt = (
        select(Agent)
        .options(selectinload(Agent.permissions))
        .where(Agent.id == payload.agent_id)
    )
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()

    user_id = agent.user_id if agent else None
    policy = None
    budget = None

    if user_id:
        # Fetch active policy
        policy_stmt = (
            select(Policy)
            .where(Policy.user_id == user_id, Policy.is_active == True)
            .order_by(Policy.version.desc())
        )
        policy = (await db.execute(policy_stmt)).scalars().first()

        # Fetch active budget
        budget_stmt = select(Budget).where(Budget.user_id == user_id)
        budget = (await db.execute(budget_stmt)).scalar_one_or_none()

    # 2. Deterministic Policy Evaluation (Law 1)
    decision: PolicyDecision = PolicyEngine.evaluate(
        amount=payload.amount,
        currency=payload.currency,
        category=payload.claimed_category,
        agent=agent,
        permission=agent.permissions if agent else None,
        policy=policy,
        budget=budget,
    )

    # 3. Persist PaymentRequest record
    # If agent does not exist, we cannot attach a foreign key, but if agent exists, save it
    payment_request_id = str(uuid.uuid4())
    if agent:
        req_record = PaymentRequest(
            id=payment_request_id,
            agent_id=agent.id,
            amount=payload.amount,
            currency=payload.currency,
            merchant_name=payload.merchant_name,
            claimed_category=payload.claimed_category,
            decision=decision.decision.value,
            decision_reasons=decision.reason_codes,
            policy_version=decision.policy_version,
        )
        db.add(req_record)
        await db.commit()

    # 4. Handle BLOCKED decision: PayPal must NEVER be called (Law 1)
    if decision.is_blocked:
        return SpendResponse(
            payment_request_id=payment_request_id,
            decision=decision.decision.value,
            decision_reasons=decision.reason_codes,
            policy_version=decision.policy_version,
            paypal_order=None,
            message="Spend request blocked by Guardian deterministic policy.",
        )

    # 5. Milestone 3: Atomic Reservation with PostgreSQL row-level locking
    reservation = None
    if budget:
        try:
            reservation = await ReservationService.reserve(
                db=db,
                budget_id=budget.id,
                amount=payload.amount,
                currency=payload.currency,
                agent_id=agent.id if agent else None,
                payment_request_id=payment_request_id,
            )
        except InsufficientFundsError:
            # Atomic reservation failed under row lock (concurrent spend consumed available budget)
            if agent:
                req_record.decision = "BLOCK"
                req_record.decision_reasons = ["BUDGET_EXHAUSTED"]
                await db.commit()
            return SpendResponse(
                payment_request_id=payment_request_id,
                decision="BLOCK",
                decision_reasons=["BUDGET_EXHAUSTED"],
                policy_version=decision.policy_version,
                paypal_order=None,
                message="Spend request blocked by Guardian deterministic policy: insufficient budget.",
            )

    # 6. Proceed to transaction creation and PayPal execution
    tx = Transaction(
        id=str(uuid.uuid4()),
        payment_request_id=payment_request_id,
        amount=payload.amount,
        currency=payload.currency,
        status="PENDING",
        merchant_name=payload.merchant_name,
        description=payload.description,
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)

    # Law 5: Call PayPal outside DB lock
    try:
        order_data = await paypal_service.create_order(
            amount=payload.amount,
            currency=payload.currency,
            custom_id=tx.id,
            description=payload.description,
        )
    except PayPalAPIError as e:
        tx.status = "FAILED"
        if reservation:
            await ReservationService.release(db, reservation.id)
        else:
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"PayPal API error during spend execution: {e.message}",
        )
    except Exception as e:
        tx.status = "FAILED"
        if reservation:
            await ReservationService.release(db, reservation.id)
        else:
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error communicating with PayPal: {str(e)}",
        )

    # Settle reservation upon confirmed PayPal execution (atomic shift from reserved to spent)
    if reservation:
        await ReservationService.settle(db, reservation.id)
    elif budget:
        budget.current_spent += payload.amount
        await db.commit()

    tx.paypal_order_id = order_data.get("id")
    tx.status = "CREATED"
    await db.commit()
    await db.refresh(tx)

    paypal_response = PayPalOrderResponse(
        order_id=tx.paypal_order_id,
        status=tx.status,
        approval_url=_extract_approval_url(order_data),
        transaction_id=tx.id,
        amount=tx.amount,
        currency=tx.currency,
    )

    return SpendResponse(
        payment_request_id=payment_request_id,
        decision=decision.decision.value,
        decision_reasons=[],
        policy_version=decision.policy_version,
        paypal_order=paypal_response,
        message="Spend request auto-approved and PayPal order placed.",
    )
