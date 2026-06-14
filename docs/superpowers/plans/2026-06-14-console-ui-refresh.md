# Console UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the static console into a polished, responsive operations UI without changing application behavior.

**Architecture:** Preserve the existing HTML IDs and JavaScript module boundaries. Improve semantic wrappers and presentation copy in `index.html`, centralize the visual system and responsive layout in `console.css`, and add only presentation-oriented state classes in existing JavaScript.

**Tech Stack:** HTML5, CSS custom properties, vanilla JavaScript, FastAPI static files

---

### Task 1: Page Structure

**Files:**
- Modify: `app/static/index.html`

- [ ] Add product branding and descriptive login copy.
- [ ] Group header controls and add accessible labels.
- [ ] Add chat empty-state content and clearer panel headings.
- [ ] Preserve every existing element ID and event handler.

### Task 2: Visual System

**Files:**
- Modify: `app/static/css/console.css`

- [ ] Replace the flat palette with layered surfaces and semantic color tokens.
- [ ] Add polished navigation, panels, cards, tables, forms, dialogs, and toast styles.
- [ ] Improve chat message, composer, sidebar, and session-list presentation.
- [ ] Add tablet, mobile, focus-visible, and reduced-motion rules.

### Task 3: Presentation Behavior

**Files:**
- Modify: `app/static/js/chat.js`

- [ ] Toggle a populated state on the message container.
- [ ] Keep the empty-state visible for new sessions and hide it when messages exist.
- [ ] Preserve streaming and run-control behavior.

### Task 4: Verification

**Files:**
- Verify: `app/static/index.html`
- Verify: `app/static/css/console.css`
- Verify: `app/static/js/chat.js`

- [ ] Run `python -m pytest -q` and require zero failures.
- [ ] Start the application and inspect login and chat layouts in the browser.
- [ ] Check desktop and mobile viewport behavior and browser console errors.

