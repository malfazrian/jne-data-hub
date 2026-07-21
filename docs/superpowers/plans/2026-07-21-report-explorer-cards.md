# Report Explorer Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh Report Explorer cards into a compact professional file-library interface.

**Architecture:** Keep the existing inline component boundary in `report_viewer.html`: CSS owns presentation, while `createFileCard()` owns generated markup and current event handlers. Add source-level regression tests because the card is generated client-side.

**Tech Stack:** Flask/Jinja, Bootstrap 5, Bootstrap Icons, vanilla JavaScript, pytest

## Global Constraints

- Do not change backend routes or response data.
- Preserve selection, favorite, recent, download, filtering, and bulk-action behavior.
- Keep three/two/one responsive grid columns.

---

### Task 1: Define the refreshed card contract

**Files:**
- Create: `tests/test_report_explorer_cards.py`
- Test: `templates/report_viewer.html`

- [ ] Write tests asserting the compact card classes, two-line filename, action cluster, metadata row, and compact download footer.
- [ ] Run `python -m pytest tests/test_report_explorer_cards.py -q` and confirm it fails because those structures are absent.

### Task 2: Implement the compact professional card

**Files:**
- Modify: `templates/report_viewer.html`
- Test: `tests/test_report_explorer_cards.py`

- [ ] Replace duplicated legacy card CSS with the new scoped card presentation and responsive states.
- [ ] Update only the markup string inside `createFileCard()` while preserving queried class names and data attributes used by event handlers.
- [ ] Run `python -m pytest tests/test_report_explorer_cards.py -q` and confirm it passes.
- [ ] Run `python -m pytest -q` and `git diff --check`.
