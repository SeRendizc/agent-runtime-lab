"""Deterministic risk derivation for proposed plan steps."""

from __future__ import annotations

from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime_lab.domain.plan import PlanStep


class RiskRule(BaseModel):
    """One Runtime-owned rule for deriving a risk tag."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tag: str = Field(min_length=1)
    path_patterns: tuple[str, ...] = ()
    tool_names: frozenset[str] = frozenset()


class RiskAssessment(BaseModel):
    """Claimed, derived and effective risks for one plan step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claimed_tags: frozenset[str]
    derived_tags: frozenset[str]
    effective_tags: frozenset[str]


class RiskEvaluator:
    """Apply trusted deterministic rules to an untrusted plan step."""

    def __init__(self, rules: tuple[RiskRule, ...]) -> None:
        self._rules = rules

    def evaluate(self, step: PlanStep) -> RiskAssessment:
        """Return risks without allowing claimed tags to erase derived tags."""

        claimed_tags = frozenset(step.risk_tags)
        derived_tags: set[str] = set()

        normalized_paths = tuple(self._normalize_path(path) for path in step.affected_paths)

        for rule in self._rules:
            matches_path = any(
                fnmatchcase(path, pattern)
                for path in normalized_paths
                for pattern in rule.path_patterns
            )
            matches_tool = bool(set(step.proposed_tools) & rule.tool_names)

            if matches_path or matches_tool:
                derived_tags.add(rule.tag)

        frozen_derived_tags = frozenset(derived_tags)

        return RiskAssessment(
            claimed_tags=claimed_tags,
            derived_tags=frozen_derived_tags,
            effective_tags=(claimed_tags | frozen_derived_tags),
        )

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = path.replace("\\", "/")

        while normalized.startswith("./"):
            normalized = normalized[2:]

        return normalized
