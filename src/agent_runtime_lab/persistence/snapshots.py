"""Canonical serialization for disposable RunState snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_runtime_lab.domain.state import RunState, RunStatus

SNAPSHOT_SCHEMA_VERSION = 2


def encode_state(state: RunState) -> str:
    """Encode every reducer-owned field using canonical JSON."""

    payload = {
        "active_gate_attempts": state.active_gate_attempts,
        "active_gate_max_attempts": state.active_gate_max_attempts,
        "active_gate_mode": state.active_gate_mode,
        "active_gate_proposal_digest": state.active_gate_proposal_digest,
        "active_gate_revision": state.active_gate_revision,
        "active_model_action_event_id": state.active_model_action_event_id,
        "active_model_invocation_id": state.active_model_invocation_id,
        "active_step_id": state.active_step_id,
        "active_tool_call_id": state.active_tool_call_id,
        "applied_event_fingerprints": state.applied_event_fingerprints,
        "failure_reason": state.failure_reason,
        "max_steps": state.max_steps,
        "next_sequence": state.next_sequence,
        "run_id": state.run_id,
        "status": state.status.value,
        "turn_index": state.turn_index,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def decode_state(state_json: str) -> RunState:
    """Decode a schema-v2 state, rejecting missing or unexpected fields."""

    payload: Any = json.loads(state_json)
    if not isinstance(payload, dict):
        raise ValueError("snapshot state must be a JSON object")

    expected_fields = {
        "active_gate_attempts",
        "active_gate_max_attempts",
        "active_gate_mode",
        "active_gate_proposal_digest",
        "active_gate_revision",
        "active_model_action_event_id",
        "active_model_invocation_id",
        "active_step_id",
        "active_tool_call_id",
        "applied_event_fingerprints",
        "failure_reason",
        "max_steps",
        "next_sequence",
        "run_id",
        "status",
        "turn_index",
    }
    if set(payload) != expected_fields:
        raise ValueError("snapshot state fields do not match schema")

    fingerprints = payload["applied_event_fingerprints"]
    if not isinstance(fingerprints, list) or any(
        not isinstance(item, list)
        or len(item) != 2
        or not all(isinstance(value, str) for value in item)
        for item in fingerprints
    ):
        raise ValueError("snapshot event fingerprints are invalid")

    return RunState(
        run_id=payload["run_id"],
        status=RunStatus(payload["status"]),
        next_sequence=payload["next_sequence"],
        turn_index=payload["turn_index"],
        max_steps=payload["max_steps"],
        active_step_id=payload["active_step_id"],
        active_model_invocation_id=payload["active_model_invocation_id"],
        active_model_action_event_id=payload["active_model_action_event_id"],
        active_tool_call_id=payload["active_tool_call_id"],
        active_gate_proposal_digest=payload["active_gate_proposal_digest"],
        active_gate_revision=payload["active_gate_revision"],
        active_gate_mode=payload["active_gate_mode"],
        active_gate_attempts=payload["active_gate_attempts"],
        active_gate_max_attempts=payload["active_gate_max_attempts"],
        failure_reason=payload["failure_reason"],
        applied_event_fingerprints=tuple(tuple(item) for item in fingerprints),
    )


def digest_state(state_json: str) -> str:
    """Return the integrity digest stored beside serialized state."""

    return hashlib.sha256(state_json.encode("utf-8")).hexdigest()
