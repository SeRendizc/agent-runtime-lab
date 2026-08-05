"""Planning domain contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanStep(BaseModel):
    """One bounded implementation step proposed for a task."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    step_id: str = Field(
        min_length=1,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
    )
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)

    affected_paths: tuple[str, ...] = ()
    proposed_tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    risk_tags: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def step_must_not_depend_on_itself(self) -> PlanStep:
        """Reject a direct self-dependency."""

        if self.step_id in self.depends_on:
            raise ValueError("a plan step cannot depend on itself")

        return self
