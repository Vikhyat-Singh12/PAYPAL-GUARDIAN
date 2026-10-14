import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.agent import Agent, AgentPermission
from app.models.budget import Budget
from app.models.policy import Policy
from app.models.reservation import Reservation, ReservationStatus
from app.models.user import User
from app.services.reservation import (
    InsufficientFundsError,
    InvalidReservationStateError,
    ReservationNotFoundError,
    ReservationService,
)
from tests.conftest import MockPayPalTransport


@pytest.mark.asyncio
async def test_atomic_reservation_lifecycle(db_session: AsyncSession):
    """
    Test fundamental reservation lifecycle:
    1. Reserve $300 from $1,000 budget -> reserved=$300, available=$700
    2. Settle $300 -> reserved=$0, spent=$300, available=$700
    """
    user = User(id=str(uuid.uuid4()), email="lifecycle@test.com")
    budget = Budget(
        id=str(uuid.uuid4()),
        user_id=user.id,
        monthly_limit=Decimal("1000.00"),
        protected_balance=Decimal("0.00"),
        current_spent=Decimal("0.00"),
        reserved_amount=Decimal("0.00"),
    )
    db_session.add_all([user, budget])
    await db_session.commit()

    # 1. Reserve $300
    res = await ReservationService.reserve(
        db=db_session,
        budget_id=budget.id,
        amount=Decimal("300.00"),
        currency="USD",
        ttl_seconds=300,
    )
    assert res.id is not None
    assert res.status == ReservationStatus.PENDING.value
    assert res.amount == Decimal("300.00")

    await db_session.refresh(budget)
    assert budget.reserved_amount == Decimal("300.00")
    assert budget.current_spent == Decimal("0.00")
    assert budget.available_amount == Decimal("700.00")

    # 2. Settle the reservation
    settled = await ReservationService.settle(db=db_session, reservation_id=res.id)
    assert settled.status == ReservationStatus.SETTLED.value
    assert settled.settled_at is not None

    await db_session.refresh(budget)
    assert budget.reserved_amount == Decimal("0.00")
    assert budget.current_spent == Decimal("300.00")
    assert budget.available_amount == Decimal("700.00")


@pytest.mark.asyncio
async def test_atomic_reservation_release_restores_balance(db_session: AsyncSession):
    """
    Test release lifecycle:
    Reserve $400 -> Release $400 -> reserved amount returns to $0, available restored.
    """
    user = User(id=str(uuid.uuid4()), email="release@test.com")
    budget = Budget(
        id=str(uuid.uuid4()),
        user_id=user.id,
        monthly_limit=Decimal("1000.00"),
        protected_balance=Decimal("200.00"),
        current_spent=Decimal("0.00"),
        reserved_amount=Decimal("0.00"),
    )
    db_session.add_all([user, budget])
    await db_session.commit()

    assert budget.available_amount == Decimal("800.00")

    # Reserve $400
    res = await ReservationService.reserve(
        db=db_session,
        budget_id=budget.id,
        amount=Decimal("400.00"),
    )
    await db_session.refresh(budget)
    assert budget.reserved_amount == Decimal("400.00")
    assert budget.available_amount == Decimal("400.00")

    # Release $400
    released = await ReservationService.release(db=db_session, reservation_id=res.id)
    assert released.status == ReservationStatus.RELEASED.value
    assert released.released_at is not None

    await db_session.refresh(budget)
    assert budget.reserved_amount == Decimal("0.00")
    assert budget.available_amount == Decimal("800.00")


@pytest.mark.asyncio
async def test_reservation_expiry_frees_reserved_funds(db_session: AsyncSession):
    """
    Test reservation expiry:
    Overdue pending reservations automatically expire and release reserved funds back to budget.
    """
    user = User(id=str(uuid.uuid4()), email="expiry@test.com")
    budget = Budget(
        id=str(uuid.uuid4()),
        user_id=user.id,
        monthly_limit=Decimal("500.00"),
        current_spent=Decimal("0.00"),
        reserved_amount=Decimal("0.00"),
    )
    db_session.add_all([user, budget])
    await db_session.commit()

    # Create reservation with simulated expired timestamp in the past
    now = datetime.now(timezone.utc)
    res = Reservation(
        id=str(uuid.uuid4()),
        budget_id=budget.id,
        amount=Decimal("250.00"),
        currency="USD",
        status=ReservationStatus.PENDING.value,
        expires_at=now - timedelta(minutes=5),  # Expired 5 mins ago
    )
    budget.reserved_amount = Decimal("250.00")
    db_session.add(res)
    await db_session.commit()

    await db_session.refresh(budget)
    assert budget.available_amount == Decimal("250.00")

    # Sweep overdue reservations
    expired_list = await ReservationService.expire_overdue(db=db_session, budget_id=budget.id)
    assert len(expired_list) == 1
    assert expired_list[0].id == res.id
    assert expired_list[0].status == ReservationStatus.EXPIRED.value

    # Verify budget restored
    await db_session.refresh(budget)
    assert budget.reserved_amount == Decimal("0.00")
    assert budget.available_amount == Decimal("500.00")


@pytest.mark.asyncio
async def test_invalid_state_transitions(db_session: AsyncSession):
    """
    Verify state machine integrity:
    Cannot settle or release already settled/released/expired reservations.
    """
    user = User(id=str(uuid.uuid4()), email="statemachine@test.com")
    budget = Budget(
        id=str(uuid.uuid4()),
        user_id=user.id,
        monthly_limit=Decimal("500.00"),
    )
    db_session.add_all([user, budget])
    await db_session.commit()

    res = await ReservationService.reserve(
        db=db_session,
        budget_id=budget.id,
        amount=Decimal("100.00"),
    )

    # Settle once
    await ReservationService.settle(db=db_session, reservation_id=res.id)

    # Attempting to settle again must fail
    with pytest.raises(InvalidReservationStateError):
        await ReservationService.settle(db=db_session, reservation_id=res.id)

    # Attempting to release a settled reservation must fail
    with pytest.raises(InvalidReservationStateError):
        await ReservationService.release(db=db_session, reservation_id=res.id)


@pytest.mark.asyncio
async def test_concurrent_reservations_race_condition():
    """
    CRITICAL MILESTONE 3 RACE CONDITION TEST:
    Verifies PostgreSQL row-level locking (SELECT ... FOR UPDATE).
    
    Scenario:
    - Budget limit: $100.00.
    - 10 concurrent workers simultaneously attempt to reserve $20.00 each.
    - Exactly 5 workers must succeed ($20 * 5 = $100.00).
    - Exactly 5 workers must fail with InsufficientFundsError.
    - Final reserved_amount must be exactly $100.00, NEVER $120.00 or more!
    """
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    user_id = str(uuid.uuid4())
    budget_id = str(uuid.uuid4())

    async with session_factory() as setup_session:
        user = User(id=user_id, email=f"race-{user_id[:8]}@test.com")
        budget = Budget(
            id=budget_id,
            user_id=user.id,
            monthly_limit=Decimal("100.00"),
            protected_balance=Decimal("0.00"),
            current_spent=Decimal("0.00"),
            reserved_amount=Decimal("0.00"),
        )
        setup_session.add_all([user, budget])
        await setup_session.commit()

    success_count = 0
    failure_count = 0

    async def worker():
        nonlocal success_count, failure_count
        async with session_factory() as session:
            try:
                await ReservationService.reserve(
                    db=session,
                    budget_id=budget_id,
                    amount=Decimal("20.00"),
                    ttl_seconds=300,
                )
                success_count += 1
            except InsufficientFundsError:
                failure_count += 1

    # Launch 10 concurrent reservation tasks across independent database sessions
    tasks = [worker() for _ in range(10)]
    await asyncio.gather(*tasks)

    assert success_count == 5, f"Expected exactly 5 successes, got {success_count}"
    assert failure_count == 5, f"Expected exactly 5 failures, got {failure_count}"

    # Verify final budget state in database
    async with session_factory() as verify_session:
        stmt = select(Budget).where(Budget.id == budget_id)
        budget = (await verify_session.execute(stmt)).scalar_one()
        assert budget.reserved_amount == Decimal("100.00")
        assert budget.available_amount == Decimal("0.00")

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_spend_api_race_condition(
    async_client: httpx.AsyncClient,
    mock_paypal_transport: MockPayPalTransport,
    db_session: AsyncSession,
):
    """
    CRITICAL END-TO-END MILESTONE 3 RACE CONDITION TEST:
    Verifies that concurrent /api/v1/spend requests cannot double-spend or exceed budget.
    
    Scenario:
    - Budget available: $100.00.
    - Policy max transaction: $50.00.
    - 5 concurrent requests of $30.00 each are submitted to /api/v1/spend.
    - Exactly 3 requests must be AUTO_APPROVED ($30 * 3 = $90.00 <= $100.00).
    - Exactly 2 requests must be BLOCKED with BUDGET_EXHAUSTED.
    - PayPal must be called exactly 3 times (Law 1 & Law 5).
    """
    # 1. Register agent
    agent_res = await async_client.post(
        "/api/v1/agents",
        json={"name": "RaceAgent", "permissions": {"max_transaction_amount": "50.00"}},
    )
    agent_id = agent_res.json()["id"]

    # 2. Set policy
    await async_client.post("/api/v1/policies", json={"max_auto_transaction": "50.00"})

    # 3. Set budget with available $100.00
    await async_client.post(
        "/api/v1/budgets",
        json={"monthly_limit": "100.00", "protected_balance": "0.00"},
    )

    initial_paypal_calls = mock_paypal_transport.order_creation_calls

    payload = {
        "agent_id": agent_id,
        "amount": "30.00",
        "currency": "USD",
        "merchant_name": "Concurrent Store",
        "claimed_category": "supplies",
        "description": "Concurrent reservation test",
    }

    # Submit 5 requests concurrently
    responses = await asyncio.gather(*[
        async_client.post("/api/v1/spend", json=payload)
        for _ in range(5)
    ])

    decisions = [r.json()["decision"] for r in responses]
    approved_count = decisions.count("AUTO_APPROVE")
    blocked_count = decisions.count("BLOCK")

    assert approved_count == 3, f"Expected 3 approved, got {approved_count} (Decisions: {decisions})"
    assert blocked_count == 2, f"Expected 2 blocked, got {blocked_count} (Decisions: {decisions})"

    # Verify PayPal call count
    assert mock_paypal_transport.order_creation_calls == initial_paypal_calls + 3

    # Verify budget in DB
    budget_res = await async_client.get("/api/v1/budgets")
    budget_data = budget_res.json()
    assert Decimal(budget_data["current_spent"]) == Decimal("90.00")
    assert Decimal(budget_data["reserved_amount"]) == Decimal("0.00")
    assert Decimal(budget_data["available_amount"]) == Decimal("10.00")
