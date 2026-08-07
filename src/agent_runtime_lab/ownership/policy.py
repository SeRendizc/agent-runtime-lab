"""Ownership policy and plan-step classification contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime_lab.domain.plan import PlanStep
from agent_runtime_lab.ownership.risk_evaluator import (
    RiskAssessment,
    RiskEvaluator,
)


class OwnershipMode(StrEnum):
    """Minimum level of human ownership required for a step."""

    AUTO = "auto"
    PAIR = "pair"
    USER_GATE = "user_gate"


_MODE_ORDER = {
    OwnershipMode.AUTO: 0,
    OwnershipMode.PAIR: 1,
    OwnershipMode.USER_GATE: 2,
}


def _more_restrictive(
    left: OwnershipMode,
    right: OwnershipMode,
) -> OwnershipMode:
    """Return the stricter of two ownership modes."""

    if _MODE_ORDER[left] >= _MODE_ORDER[right]:
        return left

    return right


class OwnershipRule(BaseModel):
    """Runtime-owned mapping from risks to minimum ownership."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    risk_tags: frozenset[str]
    minimum_mode: OwnershipMode
    reason: str = Field(min_length=1)


class OwnershipPolicy(BaseModel):
    """Deterministic policy for mapping effective risks to ownership."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    default_mode: OwnershipMode = OwnershipMode.AUTO
    rules: tuple[OwnershipRule, ...] = ()

    def matching_rules(
        self,
        effective_tags: frozenset[str],
    ) -> tuple[OwnershipRule, ...]:
        """Return rules whose risk tags intersect the effective risks."""

        return tuple(rule for rule in self.rules if rule.risk_tags & effective_tags)

    def minimum_mode_for(
        self,
        effective_tags: frozenset[str],
    ) -> OwnershipMode:
        """Return the strictest mode required by the effective risks."""

        mode = self.default_mode

        for rule in self.matching_rules(effective_tags):
            mode = _more_restrictive(mode, rule.minimum_mode)

        return mode

    def reasons_for(
        self,
        effective_tags: frozenset[str],
    ) -> tuple[str, ...]:
        """Return explainable policy reasons."""

        matched = self.matching_rules(effective_tags)

        if not matched:
            return (f"no risk rule matched; using default {self.default_mode.value}",)

        return tuple(rule.reason for rule in matched)


@dataclass(frozen=True, slots=True)
class OwnershipContext:
    """Trusted inputs used during plan-step classification."""

    risk_evaluator: RiskEvaluator
    policy: OwnershipPolicy
    minimum_mode: OwnershipMode = OwnershipMode.AUTO


class OwnershipDecision(BaseModel):
    """Explainable result of classifying one plan step."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    step_id: str
    mode: OwnershipMode
    policy_minimum_mode: OwnershipMode
    context_minimum_mode: OwnershipMode
    risk_assessment: RiskAssessment
    reasons: tuple[str, ...]


def classify_step(
    step: PlanStep,
    context: OwnershipContext,
) -> OwnershipDecision:
    """Classify a step using trusted risks and policy constraints."""

    assessment = context.risk_evaluator.evaluate(step)

    policy_mode = context.policy.minimum_mode_for(
        assessment.effective_tags,
    )

    final_mode = _more_restrictive(
        policy_mode,
        context.minimum_mode,
    )

    reasons = list(
        context.policy.reasons_for(
            assessment.effective_tags,
        )
    )

    if context.minimum_mode != OwnershipMode.AUTO:
        reasons.append(f"global minimum mode requires {context.minimum_mode.value}")

    return OwnershipDecision(
        step_id=step.step_id,
        mode=final_mode,
        policy_minimum_mode=policy_mode,
        context_minimum_mode=context.minimum_mode,
        risk_assessment=assessment,
        reasons=tuple(reasons),
    )
