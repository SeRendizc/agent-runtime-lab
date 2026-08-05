from __future__ import annotations

from agent_runtime_lab.domain.plan import PlanStep
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


def make_evaluator() -> RiskEvaluator:
    return RiskEvaluator(
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


def test_core_path_is_derived_when_model_reports_no_risk() -> None:
    step = make_step(
        affected_paths=("src/model/kv_cache.py",),
    )

    assessment = make_evaluator().evaluate(step)

    assert assessment.claimed_tags == frozenset()
    assert assessment.derived_tags == frozenset({"core_learning_path"})
    assert assessment.effective_tags == frozenset({"core_learning_path"})


def test_claimed_and_derived_risks_are_combined() -> None:
    step = make_step(
        affected_paths=("src/model/kv_cache.py",),
        risk_tags=frozenset({"model_reported_risk"}),
    )

    assessment = make_evaluator().evaluate(step)

    assert assessment.effective_tags == frozenset(
        {
            "core_learning_path",
            "model_reported_risk",
        }
    )


def test_model_cannot_erase_runtime_derived_risk() -> None:
    step = make_step(
        affected_paths=("src/agent_runtime_lab/persistence/store.py",),
        risk_tags=frozenset(),
    )

    assessment = make_evaluator().evaluate(step)

    assert "sensitive_runtime_path" in (assessment.derived_tags)
    assert "sensitive_runtime_path" in (assessment.effective_tags)


def test_proposed_destructive_tool_derives_risk() -> None:
    step = make_step(
        proposed_tools=("delete_file",),
    )

    assessment = make_evaluator().evaluate(step)

    assert assessment.derived_tags == frozenset({"destructive_tool"})


def test_windows_style_path_is_normalized() -> None:
    step = make_step(
        affected_paths=(r".\src\model\kv_cache.py",),
    )

    assessment = make_evaluator().evaluate(step)

    assert assessment.derived_tags == frozenset({"core_learning_path"})


def test_same_input_produces_same_assessment() -> None:
    step = make_step(
        affected_paths=("src/model/kv_cache.py",),
        risk_tags=frozenset({"reported"}),
    )
    evaluator = make_evaluator()

    first = evaluator.evaluate(step)
    second = evaluator.evaluate(step)

    assert second == first
