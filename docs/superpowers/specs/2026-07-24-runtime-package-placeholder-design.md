# Runtime Package Placeholder Design

## Goal

Create the smallest importable Python package scaffold for Agent Runtime Lab so
PyCharm can use a project-specific virtual environment and an editable install
can resolve `agent_runtime_lab`.

## Changes

- Add `src/agent_runtime_lab/__init__.py` as an intentionally empty package
  entry point.
- Replace the empty setuptools package list with package discovery rooted at
  `src`.

## Boundaries

- Do not implement events, state transitions, reducers, persistence, replay,
  authorization, or other runtime semantics.
- Do not modify PyCharm's untracked `.idea/` directory.
- Do not add tests for behavior that does not exist.

## Verification

- Install the project in editable mode with the `dev` extra.
- Import `agent_runtime_lab` using the selected project interpreter.
- Run Ruff to confirm the placeholder package and configuration are clean.
