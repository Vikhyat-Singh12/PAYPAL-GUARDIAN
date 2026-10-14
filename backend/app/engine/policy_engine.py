from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.models.agent import Agent, AgentPermission
from app.models.budget import Budget
from app.models.policy import Policy


class DecisionEnum(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"  # For future milestones
    BLOCK = "BLOCK"


class ReasonCode(str, Enum):
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_REVOKED = "AGENT_REVOKED"
    AGENT_SUSPENDED = "AGENT_SUSPENDED"
    PERMISSION_NOT_FOUND = "PERMISSION_NOT_FOUND"
    PERMISSION_NOT_STARTED = "PERMISSION_NOT_STARTED"
    PERMISSION_EXPIRED = "PERMISSION_EXPIRED"
    CATEGORY_BLOCKED = "CATEGORY_BLOCKED"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    TRANSACTION_LIMIT_EXCEEDED = "TRANSACTION_LIMIT_EXCEEDED"
    POLICY_LIMIT_EXCEEDED = "POLICY_LIMIT_EXCEEDED"
    CATEGORY_LIMIT_EXCEEDED = "CATEGORY_LIMIT_EXCEEDED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    PROTECTED_BALANCE_VIOLATION = "PROTECTED_BALANCE_VIOLATION"


class PolicyDecision(BaseModel):
    decision: DecisionEnum
    reason_codes: List[str] = Field(default_factory=list)
    policy_version: Optional[int] = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.decision == DecisionEnum.AUTO_APPROVE

    @property
    def is_blocked(self) -> bool:
        return self.decision == DecisionEnum.BLOCK


class PolicyEngine:
    """
    Deterministic Policy Engine (Law 1).
    Evaluates agent status, permissions, limits, category restrictions,
    and budget availability without any LLM interpretation at runtime.
    """

    @staticmethod
    def evaluate(
        amount: Decimal,
        currency: str,
        category: str,
        agent: Optional[Agent] = None,
        permission: Optional[AgentPermission] = None,
        policy: Optional[Policy] = None,
        budget: Optional[Budget] = None,
        current_time: Optional[datetime] = None,
    ) -> PolicyDecision:
        now = current_time or datetime.now(timezone.utc)
        reasons: List[str] = []
        details: Dict[str, Any] = {
            "requested_amount": str(amount),
            "currency": currency,
            "category": category,
        }

        normalized_category = category.strip().lower()

        # 1. Agent existence check
        if agent is None:
            return PolicyDecision(
                decision=DecisionEnum.BLOCK,
                reason_codes=[ReasonCode.AGENT_NOT_FOUND.value],
                details={"error": "Agent does not exist in Guardian registry."},
            )

        details["agent_id"] = agent.id
        details["agent_name"] = agent.name
        details["agent_status"] = agent.status

        # 2. Agent status check
        if agent.status == "REVOKED":
            reasons.append(ReasonCode.AGENT_REVOKED.value)
        elif agent.status == "SUSPENDED":
            reasons.append(ReasonCode.AGENT_SUSPENDED.value)

        # 3. Agent permissions check
        if permission is None:
            reasons.append(ReasonCode.PERMISSION_NOT_FOUND.value)
        else:
            # Time-validity check
            if permission.starts_at and now < permission.starts_at:
                reasons.append(ReasonCode.PERMISSION_NOT_STARTED.value)
            if permission.expires_at and now > permission.expires_at:
                reasons.append(ReasonCode.PERMISSION_EXPIRED.value)

            # Blocked categories at agent level
            blocked_agent_categories = [c.lower() for c in (permission.blocked_categories or [])]
            if normalized_category in blocked_agent_categories:
                reasons.append(ReasonCode.CATEGORY_BLOCKED.value)
                details["blocked_by"] = "agent_permission"

            # Whitelist categories at agent level (if defined)
            if permission.allowed_categories:
                allowed_agent_categories = [c.lower() for c in permission.allowed_categories]
                if normalized_category not in allowed_agent_categories:
                    reasons.append(ReasonCode.CATEGORY_NOT_ALLOWED.value)
                    details["allowed_categories"] = permission.allowed_categories

            # Agent per-transaction limit
            if amount > permission.max_transaction_amount:
                reasons.append(ReasonCode.TRANSACTION_LIMIT_EXCEEDED.value)
                details["agent_max_transaction"] = str(permission.max_transaction_amount)

        # 4. Global Policy checks
        policy_version = None
        if policy is not None:
            policy_version = policy.version
            details["policy_version"] = policy.version

            # Global blocked categories
            global_blocked = [c.lower() for c in (policy.blocked_categories or [])]
            if normalized_category in global_blocked and ReasonCode.CATEGORY_BLOCKED.value not in reasons:
                reasons.append(ReasonCode.CATEGORY_BLOCKED.value)
                details["blocked_by"] = "global_policy"

            # Global max auto transaction limit
            if amount > policy.max_auto_transaction:
                reasons.append(ReasonCode.POLICY_LIMIT_EXCEEDED.value)
                details["policy_max_auto_transaction"] = str(policy.max_auto_transaction)

            # Specific category limit
            if policy.category_limits and normalized_category in policy.category_limits:
                cat_limit = Decimal(str(policy.category_limits[normalized_category]))
                if amount > cat_limit:
                    reasons.append(ReasonCode.CATEGORY_LIMIT_EXCEEDED.value)
                    details["category_limit"] = str(cat_limit)

        # 5. Basic Budget checks
        if budget is not None:
            available = budget.available_amount
            details["budget_available"] = str(available)
            details["budget_monthly_limit"] = str(budget.monthly_limit)
            details["budget_current_spent"] = str(budget.current_spent)

            if amount > available:
                reasons.append(ReasonCode.BUDGET_EXHAUSTED.value)

        # Final Deterministic Decision
        if reasons:
            return PolicyDecision(
                decision=DecisionEnum.BLOCK,
                reason_codes=reasons,
                policy_version=policy_version,
                details=details,
            )

        return PolicyDecision(
            decision=DecisionEnum.AUTO_APPROVE,
            reason_codes=[],
            policy_version=policy_version,
            details=details,
        )
