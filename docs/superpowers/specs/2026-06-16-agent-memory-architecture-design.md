# Agent Memory Architecture Design

## Goal

Build a durable memory system for agents that combines rolling session summaries, scoped long-term memory, retrieval, and shared memory tools for main agents and sub-agents.

## Scope

This implementation adds:

- Session summaries that compact conversation history.
- Long-term `memory_items` scoped to user, workspace, session, or run.
- Retrieval with keyword fallback and optional embedding vectors.
- `memory_search`, `memory_write`, `memory_update`, and `memory_forget` tools.
- Runner context injection of relevant memories.
- Post-run automatic summary and memory capture.
- Tenant, workspace, session, and user visibility checks.

This implementation does not add a full visual memory-management UI. Operators can use the API and agent tools.

## Architecture

Memory is stored in two new tables:

- `session_summaries`: one row per session, storing a rolling summary and the last message covered.
- `memory_items`: tenant-owned memories with scope, kind, content, source, confidence, expiration, soft-delete, and optional embedding JSON.

`MemoryService` owns all read/write behavior. It uses deterministic local summaries by default and leaves embedding generation best-effort through OpenAI-compatible configuration. If embeddings or pgvector are unavailable, keyword scoring still works.

The Runner builds context from:

1. Current agent system prompt and enabled Skills.
2. Session summary.
3. Retrieved relevant memories.
4. Recent user/assistant messages.

At run completion, the Runner writes the assistant message, updates the session summary, and captures low-risk memory candidates from the latest user/assistant exchange.

## Security

All memory rows carry `tenant_id` and are covered by existing RLS policy generation. Application queries additionally filter by:

- `workspace_id` for workspace/session/run memories.
- `user_id` for user memories.
- `session_id` for session and run memories.
- `deleted_at IS NULL`.
- `expires_at IS NULL OR expires_at > now`.

Secret-like content is not auto-captured.

## APIs And Tools

HTTP API:

- `GET /v1/memories`
- `POST /v1/memories`
- `PATCH /v1/memories/{memory_id}`
- `DELETE /v1/memories/{memory_id}`

Agent tools:

- `memory_search(query, scope?, limit?)`
- `memory_write(scope, kind, title, content, confidence?)`
- `memory_update(memory_id, title?, content?, confidence?, pinned?)`
- `memory_forget(memory_id)`

Sub-agents receive the same tool schemas as normal tools, so they can use shared memory without seeing the entire main conversation.

## Testing

Tests cover:

- Scoped memory visibility and soft delete.
- Retrieval ranking.
- Session summary update and context injection.
- Agent tool registration and execution.
- Run completion triggering summary and capture.
