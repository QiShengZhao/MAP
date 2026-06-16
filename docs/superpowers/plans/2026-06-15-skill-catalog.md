# Curated Skill Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fixed official skill catalog and safe remote Markdown importer to the Skills page.

**Architecture:** Keep catalog metadata and remote parsing in a focused platform service. Expose authenticated catalog, preview, and install endpoints through the existing skill router, then extend the vanilla JavaScript Skills page with catalog cards and URL preview.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, HTTPX, vanilla JavaScript, CSS

---

### Task 1: Catalog And Parser

**Files:**
- Create: `app/platform_services/skill_catalog.py`
- Test: `tests/test_skill_catalog.py`

- [ ] Define immutable curated entries with direct HTTPS Markdown URLs.
- [ ] Parse `name` and `description` from optional YAML-style frontmatter.
- [ ] Reject empty, oversized, non-HTTPS, redirected, or non-text responses.
- [ ] Reuse resolved-address SSRF protection before fetching.

### Task 2: Catalog API

**Files:**
- Modify: `app/api/routes_skill.py`
- Test: `tests/test_skill_catalog.py`

- [ ] Add `GET /v1/skills/catalog`.
- [ ] Add `POST /v1/skills/import/preview`.
- [ ] Add `POST /v1/skills/import`.
- [ ] Reject duplicate names with HTTP 409 and preserve tenant isolation.
- [ ] Return full descriptions and instructions from the existing list endpoint.

### Task 3: Skills Page

**Files:**
- Modify: `app/static/js/config.js`
- Modify: `app/static/css/console.css`
- Modify: `app/static/index.html`

- [ ] Render curated catalog cards before the installed skill table.
- [ ] Display publisher, category, and source links.
- [ ] Add one-click install with installed-state feedback.
- [ ] Add custom URL preview and install modal.

### Task 4: Verification

**Files:**
- Verify: `tests/test_skill_catalog.py`
- Verify: `app/static/js/config.js`

- [ ] Run focused catalog tests.
- [ ] Run `python -m pytest -q`.
- [ ] Rebuild the API container and verify the catalog endpoint and Skills page.

