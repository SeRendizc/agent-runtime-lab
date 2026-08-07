from __future__ import annotations

from agent_runtime_lab.domain.plan import PlanStep
from agent_runtime_lab.ownership.policy import (
    OwnershipContext,
    OwnershipMode,
    OwnershipPolicy,
    OwnershipRule,
    classify_step,
)
from agent_runtime_lab.ownership.risk_evaluator import (
    RiskEvaluator,
    RiskRule,
)


def make_step(
    *,
    affected_paths: tuple[str, ...] = (),
    proposed_tools: tuple[str, ...] = (),
    risk_tags: frozenset[str] = frozenset(),
) -> PlanStep:
    return PlanStep(
        step_id="step-1",
        title="Update implementation",
        description="Modify one part of the implementation.",
        affected_paths=affected_paths,
        proposed_tools=proposed_tools,
        risk_tags=risk_tags,
    )


def make_context(
    *,
    minimum_mode: OwnershipMode = OwnershipMode.AUTO,
) -> OwnershipContext:
    evaluator = RiskEvaluator(
        rules=(
            RiskRule(
                tag="core_learning_path",
                path_patterns=("src/model/**",),
            ),
            RiskRule(
                tag="sensitive_runtime_path",
                path_patterns=("src/agent_runtime_lab/persistence/**",),
            ),
            RiskRule(
                tag="destructive_tool",
                tool_names=frozenset({"delete_file"}),
            ),
        )
    )

    policy = OwnershipPolicy(
        rules=(
            OwnershipRule(
                risk_tags=frozenset({"core_learning_path"}),
                minimum_mode=OwnershipMode.USER_GATE,
                reason="core learning path requires explicit user ownership",
            ),
            OwnershipRule(
                risk_tags=frozenset({"sensitive_runtime_path"}),
                minimum_mode=OwnershipMode.PAIR,
                reason="runtime persistence path requires paired review",
            ),
            OwnershipRule(
                risk_tags=frozenset({"destructive_tool"}),
                minimum_mode=OwnershipMode.USER_GATE,
                reason="destructive tools require explicit user approval",
            ),
        )
    )

    return OwnershipContext(
        risk_evaluator=evaluator,
        policy=policy,
        minimum_mode=minimum_mode,
    )


def test_readme_path_is_auto() -> None:
    decision = classify_step(
        make_step(affected_paths=("README.md",)),
        make_context(),
    )

    assert decision.mode is OwnershipMode.AUTO
    assert decision.policy_minimum_mode is OwnershipMode.AUTO
    assert decision.risk_assessment.effective_tags == frozenset()


def test_core_learning_path_requires_user_gate() -> None:
    decision = classify_step(
        make_step(affected_paths=("src/model/kv_cache.py",)),
        make_context(),
    )

    assert decision.mode is OwnershipMode.USER_GATE
    assert decision.policy_minimum_mode is OwnershipMode.USER_GATE
    assert "core_learning_path" in (decision.risk_assessment.effective_tags)


def test_global_pair_minimum_upgrades_normal_path_to_pair() -> None:
    decision = classify_step(
        make_step(affected_paths=("README.md",)),
        make_context(minimum_mode=OwnershipMode.PAIR),
    )

    assert decision.policy_minimum_mode is OwnershipMode.AUTO
    assert decision.context_minimum_mode is OwnershipMode.PAIR
    assert decision.mode is OwnershipMode.PAIR
    assert decision.reasons[-1] == "global minimum mode requires pair"


def test_global_pair_does_not_lower_core_path_from_user_gate() -> None:
    decision = classify_step(
        make_step(affected_paths=("src/model/kv_cache.py",)),
        make_context(minimum_mode=OwnershipMode.PAIR),
    )

    assert decision.policy_minimum_mode is OwnershipMode.USER_GATE
    assert decision.mode is OwnershipMode.USER_GATE


def test_empty_claimed_risks_cannot_erase_derived_risk() -> None:
    decision = classify_step(
        make_step(
            affected_paths=("src/model/kv_cache.py",),
            risk_tags=frozenset(),
        ),
        make_context(),
    )

    assert decision.risk_assessment.claimed_tags == frozenset()
    assert decision.risk_assessment.derived_tags == frozenset({"core_learning_path"})
    assert decision.mode is OwnershipMode.USER_GATE


def test_claimed_risk_can_only_upgrade_ownership() -> None:
    decision = classify_step(
        make_step(
            affected_paths=("README.md",),
            risk_tags=frozenset({"sensitive_runtime_path"}),
        ),
        make_context(),
    )

    assert decision.risk_assessment.derived_tags == frozenset()
    assert decision.risk_assessment.effective_tags == frozenset({"sensitive_runtime_path"})
    assert decision.mode is OwnershipMode.PAIR


def test_destructive_tool_requires_user_gate() -> None:
    decision = classify_step(
        make_step(proposed_tools=("delete_file",)),
        make_context(),
    )

    assert decision.mode is OwnershipMode.USER_GATE
    assert decision.risk_assessment.derived_tags == frozenset({"destructive_tool"})


def test_same_input_produces_identical_decision() -> None:
    step = make_step(
        affected_paths=("src/model/kv_cache.py",),
        risk_tags=frozenset({"reported"}),
    )
    context = make_context()

    first = classify_step(step, context)
    second = classify_step(step, context)

    assert second == first
