from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest

from app.engine.policy_engine import DecisionEnum, PolicyDecision, PolicyEngine, ReasonCode
from app.models.agent import Agent, AgentPermission
from app.models.budget import Budget
from app.models.policy import Policy


def make_test_agent(status="ACTIVE", max_tx=Decimal("2000.00"), blocked=None, allowed=None, expired=False):
    agent = Agent(id="agent-001", user_id="user-001", name="ShoppingAgent", status=status)
    now = datetime.now(timezone.utc)
    expires_at = now - timedelta(hours=1) if expired else now + timedelta(days=30)
    agent.permissions = AgentPermission(
        id="perm-001",
        agent_id=agent.id,
        max_transaction_amount=max_tx,
        blocked_categories=blocked or [],
        allowed_categories=allowed or [],
        expires_at=expires_at,
    )
    return agent


def make_test_policy(max_auto=Decimal("2000.00"), blocked=None):
    return Policy(
        id="policy-001",
        user_id="user-001",
        version=1,
        max_auto_transaction=max_auto,
        blocked_categories=blocked or ["gambling", "adult", "weapons"],
    )


def make_test_budget(limit=Decimal("10000.00"), current_spent=Decimal("0.00"), protected=Decimal("0.00"), reserved=Decimal("0.00")):
    return Budget(
        id="budget-001",
        user_id="user-001",
        monthly_limit=limit,
        current_spent=current_spent,
        protected_balance=protected,
        reserved_amount=reserved,
    )


def test_auto_approve_within_limits():
    """Verify standard valid transaction is auto-approved."""
    agent = make_test_agent(max_tx=Decimal("2000.00"))
    policy = make_test_policy(max_auto=Decimal("2000.00"))
    budget = make_test_budget(limit=Decimal("10000.00"))

    decision = PolicyEngine.evaluate(
        amount=Decimal("1000.00"),
        currency="USD",
        category="groceries",
        agent=agent,
        permission=agent.permissions,
        policy=policy,
        budget=budget,
    )
    assert decision.decision == DecisionEnum.AUTO_APPROVE
    assert decision.is_approved is True
    assert len(decision.reason_codes) == 0


def test_block_when_transaction_limit_exceeded():
    """Verify ₹5,000 spend is blocked when limit is ₹2,000."""
    agent = make_test_agent(max_tx=Decimal("2000.00"))
    policy = make_test_policy(max_auto=Decimal("2000.00"))

    decision = PolicyEngine.evaluate(
        amount=Decimal("5000.00"),
        currency="USD",
        category="electronics",
        agent=agent,
        permission=agent.permissions,
        policy=policy,
    )
    assert decision.decision == DecisionEnum.BLOCK
    assert decision.is_blocked is True
    assert ReasonCode.TRANSACTION_LIMIT_EXCEEDED.value in decision.reason_codes
    assert ReasonCode.POLICY_LIMIT_EXCEEDED.value in decision.reason_codes


def test_block_when_category_blocked():
    """Verify blocked categories like gambling are rejected."""
    agent = make_test_agent()
    policy = make_test_policy(blocked=["gambling", "weapons"])

    decision = PolicyEngine.evaluate(
        amount=Decimal("50.00"),
        currency="USD",
        category="gambling",
        agent=agent,
        permission=agent.permissions,
        policy=policy,
    )
    assert decision.decision == DecisionEnum.BLOCK
    assert ReasonCode.CATEGORY_BLOCKED.value in decision.reason_codes


def test_block_when_agent_revoked():
    """Verify revoked agents are strictly blocked."""
    agent = make_test_agent(status="REVOKED")

    decision = PolicyEngine.evaluate(
        amount=Decimal("10.00"),
        currency="USD",
        category="books",
        agent=agent,
        permission=agent.permissions,
    )
    assert decision.decision == DecisionEnum.BLOCK
    assert ReasonCode.AGENT_REVOKED.value in decision.reason_codes


def test_block_when_permission_expired():
    """Verify time-expired agent authority is blocked."""
    agent = make_test_agent(expired=True)

    decision = PolicyEngine.evaluate(
        amount=Decimal("10.00"),
        currency="USD",
        category="books",
        agent=agent,
        permission=agent.permissions,
    )
    assert decision.decision == DecisionEnum.BLOCK
    assert ReasonCode.PERMISSION_EXPIRED.value in decision.reason_codes


def test_block_when_budget_exhausted():
    """Verify transactions exceeding available budget are blocked."""
    agent = make_test_agent(max_tx=Decimal("5000.00"))
    policy = make_test_policy(max_auto=Decimal("5000.00"))
    # Limit: 10,000, Already spent: 9,500 -> Available: 500
    budget = make_test_budget(limit=Decimal("10000.00"), current_spent=Decimal("9500.00"))

    decision = PolicyEngine.evaluate(
        amount=Decimal("1000.00"),
        currency="USD",
        category="groceries",
        agent=agent,
        permission=agent.permissions,
        policy=policy,
        budget=budget,
    )
    assert decision.decision == DecisionEnum.BLOCK
    assert ReasonCode.BUDGET_EXHAUSTED.value in decision.reason_codes
