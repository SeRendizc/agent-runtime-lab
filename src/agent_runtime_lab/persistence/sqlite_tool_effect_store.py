"""SQLite-backed persistence for durable tool-effect facts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_runtime_lab.domain.errors import (
    DuplicateToolEffectConflictError,
    MissingToolIntentError,
)
from agent_runtime_lab.domain.tool_effects import (
    ToolIntent,
    ToolOutcome,
    ToolReceipt,
)


class SQLiteToolEffectStore:
    """Persist tool intents before execution and receipts after execution."""

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(str(database_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_intents (
                effect_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                UNIQUE (run_id, tool_call_id)
            );

            CREATE INDEX IF NOT EXISTS idx_tool_intents_run_id
            ON tool_intents (run_id);

            CREATE TABLE IF NOT EXISTS tool_receipts (
                effect_id TEXT PRIMARY KEY,
                outcome TEXT NOT NULL
                    CHECK (outcome IN ('succeeded', 'failed')),
                output_json TEXT NOT NULL,
                FOREIGN KEY (effect_id)
                    REFERENCES tool_intents (effect_id)
            );
            """
        )
        self._connection.commit()

    def save_intent(self, intent: ToolIntent) -> None:
        """Persist an intent or accept an exact redelivery."""

        self._connection.execute("BEGIN IMMEDIATE")

        try:
            existing = self._connection.execute(
                """
                SELECT
                    effect_id,
                    run_id,
                    tool_call_id,
                    tool_name,
                    arguments_json
                FROM tool_intents
                WHERE effect_id = ?
                """,
                (intent.effect_id,),
            ).fetchone()

            if existing is not None:
                persisted = self._intent_from_row(existing)

                if persisted != intent:
                    raise DuplicateToolEffectConflictError(
                        f"effect_id {intent.effect_id!r} was reused with a different intent"
                    )

                self._connection.commit()
                return

            self._connection.execute(
                """
                INSERT INTO tool_intents (
                    effect_id,
                    run_id,
                    tool_call_id,
                    tool_name,
                    arguments_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    intent.effect_id,
                    intent.run_id,
                    intent.tool_call_id,
                    intent.tool_name,
                    intent.arguments_json,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def load_intent(self, effect_id: str) -> ToolIntent | None:
        """Load one persisted intent by durable effect identity."""

        row = self._connection.execute(
            """
            SELECT
                effect_id,
                run_id,
                tool_call_id,
                tool_name,
                arguments_json
            FROM tool_intents
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()

        if row is None:
            return None

        return self._intent_from_row(row)

    def save_receipt(self, receipt: ToolReceipt) -> None:
        """Persist a receipt only after its corresponding intent exists."""

        self._connection.execute("BEGIN IMMEDIATE")

        try:
            existing = self._connection.execute(
                """
                SELECT
                    effect_id,
                    outcome,
                    output_json
                FROM tool_receipts
                WHERE effect_id = ?
                """,
                (receipt.effect_id,),
            ).fetchone()

            if existing is not None:
                persisted = self._receipt_from_row(existing)

                if persisted != receipt:
                    raise DuplicateToolEffectConflictError(
                        f"effect_id {receipt.effect_id!r} was reused with a different receipt"
                    )

                self._connection.commit()
                return

            intent_exists = self._connection.execute(
                """
                SELECT 1
                FROM tool_intents
                WHERE effect_id = ?
                """,
                (receipt.effect_id,),
            ).fetchone()

            if intent_exists is None:
                raise MissingToolIntentError(
                    f"no intent exists for effect_id {receipt.effect_id!r}"
                )

            self._connection.execute(
                """
                INSERT INTO tool_receipts (
                    effect_id,
                    outcome,
                    output_json
                )
                VALUES (?, ?, ?)
                """,
                (
                    receipt.effect_id,
                    receipt.outcome.value,
                    receipt.output_json,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def load_receipt(self, effect_id: str) -> ToolReceipt | None:
        """Load one persisted receipt by durable effect identity."""

        row = self._connection.execute(
            """
            SELECT
                effect_id,
                outcome,
                output_json
            FROM tool_receipts
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()

        if row is None:
            return None

        return self._receipt_from_row(row)

    @staticmethod
    def _intent_from_row(row: sqlite3.Row) -> ToolIntent:
        return ToolIntent(
            effect_id=row["effect_id"],
            run_id=row["run_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            arguments_json=row["arguments_json"],
        )

    @staticmethod
    def _receipt_from_row(row: sqlite3.Row) -> ToolReceipt:
        return ToolReceipt(
            effect_id=row["effect_id"],
            outcome=ToolOutcome(row["outcome"]),
            output_json=row["output_json"],
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteToolEffectStore:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()
