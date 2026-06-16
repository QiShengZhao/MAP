# Agent Memory Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scoped long-term agent memory, rolling session summaries, retrieval, and shared agent memory tools.

**Architecture:** Create SQLAlchemy models and Alembic migration for memory tables, implement a `MemoryService`, expose HTTP APIs, register memory tools, and integrate Runner context construction and completion hooks.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, PostgreSQL/SQLite-compatible JSON storage, optional OpenAI-compatible embeddings, pytest.

---

### Task 1: Storage And Service

**Files:**
- Modify: `app/domain/models.py`
- Create: `app/memory/service.py`
- Create: `alembic/versions/20260616_0003_agent_memory.py`
- Test: `tests/test_memory_service.py`

- [ ] Write failing tests for create/search/soft-delete/summaries.
- [ ] Add `MemoryItem` and `SessionSummary` models.
- [ ] Implement scoped visibility and keyword retrieval.
- [ ] Add migration and RLS application.
- [ ] Run `PYTHONPATH=. pytest -q tests/test_memory_service.py`.

### Task 2: APIs And Tools

**Files:**
- Create: `app/api/routes_memory.py`
- Modify: `app/main.py`
- Modify: `app/runtime/tools.py`
- Test: `tests/test_memory_api_tools.py`

- [ ] Write failing API/tool tests.
- [ ] Add CRUD/search API.
- [ ] Register memory tools.
- [ ] Run focused tests.

### Task 3: Runner Integration

**Files:**
- Modify: `app/execution/runner.py`
- Test: `tests/test_runner_memory.py`

- [ ] Write failing tests for memory injection and post-run capture.
- [ ] Add summary/retrieval to `_build_history`.
- [ ] Add completion hook to update summary and capture memory.
- [ ] Run focused tests and full test suite.
