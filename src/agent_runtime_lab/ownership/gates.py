"""Durable references for PAIR and USER_GATE proposals."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime_lab.domain.errors import EventValidationError
from agent_runtime_lab.ownership.authorization import (
    AuthorizationDecision,
    AuthorizationOutcome,
    ToolRequest,
)
from agent_runtime_lab.ownership.policy import OwnershipMode


class GateAction(StrEnum):
    """A human decision about one exact proposal."""

    APPROVE = "approve"
    REJECT = "reject"


class GateReference(BaseModel):
    """Exact durable identity required to resolve a gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    proposal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: int = Field(ge=1)


class GateProposal(BaseModel):
    """The exact authorized request awaiting human participation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: GateReference
    step_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments_json: str
    ownership_mode: OwnershipMode
    reasons: tuple[str, ...]

    @classmethod
    def build(
        cls,
        *,
        request: ToolRequest,
        decision: AuthorizationDecision,
        revision: int,
    ) -> GateProposal:
        """Bind an escalation to one immutable request and ownership mode."""

        if decision.outcome is not AuthorizationOutcome.ESCALATE:
            raise EventValidationError("gate proposals require an escalated authorization")
        if decision.ownership_decision is None:
            raise EventValidationError("escalated authorization requires an ownership decision")
        if decision.ownership_decision.mode is OwnershipMode.AUTO:
            raise EventValidationError("auto ownership cannot create a gate proposal")
        if (
            decision.run_id,
            decision.step_id,
            decision.tool_call_id,
            decision.tool_name,
        ) != (
            request.run_id,
            request.step_id,
            request.tool_call_id,
            request.tool_name,
        ):
            raise EventValidationError("authorization identity does not match tool request")
        if revision < 1:
            raise EventValidationError("gate proposal revision must be positive")

        ownership_mode = decision.ownership_decision.mode
        digest_payload = json.dumps(
            {
                "arguments": request.arguments,
                "normalized_paths": decision.normalized_paths,
                "ownership_mode": ownership_mode.value,
                "revision": revision,
                "run_id": request.run_id,
                "step_id": request.step_id,
                "tool_call_id": request.tool_call_id,
                "tool_name": request.tool_name,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        proposal_digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()

        return cls(
            reference=GateReference(
                run_id=request.run_id,
                tool_call_id=request.tool_call_id,
                proposal_digest=proposal_digest,
                revision=revision,
            ),
            step_id=request.step_id,
            tool_name=request.tool_name,
            arguments_json=request.arguments_json,
            ownership_mode=ownership_mode,
            reasons=decision.reasons,
        )


class GateResolution(BaseModel):
    """A durable human action bound to one proposal reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: GateReference
    action: GateAction
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @classmethod
    def approve(
        cls,
        reference: GateReference,
        *,
        actor: str,
        reason: str = "approved after human review",
    ) -> GateResolution:
        return cls(
            reference=reference,
            action=GateAction.APPROVE,
            actor=actor,
            reason=reason,
        )

    @classmethod
    def reject(
        cls,
        reference: GateReference,
        *,
        actor: str,
        reason: str,
    ) -> GateResolution:
        return cls(
            reference=reference,
            action=GateAction.REJECT,
            actor=actor,
            reason=reason,
        )
