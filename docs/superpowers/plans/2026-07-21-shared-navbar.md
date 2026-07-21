# Shared Grouped Navbar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace eight duplicated navbars with one responsive Jinja partial containing six grouped dropdown menus and automatic active states.

**Architecture:** `templates/_navbar.html` becomes the single navigation source and derives active groups from Flask `request.path`. Existing full-page templates include the partial; no page layout, script, content, or footer is otherwise restructured.

**Tech Stack:** Flask/Jinja2, Bootstrap 5 dropdown and collapse components, pytest

## Global Constraints

- Preserve every existing navigation destination.
- Use six top-level groups: Hub, Master Data DS, Report Explorer, APEX, Pickup, and DWR.
- Single-item groups remain dropdowns for consistent source-based navigation.
- Active state is derived only from `request.path`; pages pass no navigation variable.
- Do not refactor pages into base-template inheritance.
- Do not change page-specific content, scripts, or footer behavior.

---

### Task 1: Shared navbar partial and route-aware active states

**Files:**
- Create: `templates/_navbar.html`
- Create: `tests/test_shared_navbar.py`

**Interfaces:**
- Produces: renderable Jinja partial `_navbar.html` requiring Flask's standard `request` context.
- Consumes: existing `static/bars.ico`, `static/images/New_Logo_JNE.png`, `static/style.css`, and Bootstrap classes already loaded by pages.

- [ ] **Step 1: Write failing structure tests**

Create a minimal Flask test app and render `_navbar.html` under `/`. Assert the six top-level labels appear once, the brand exists, the collapse toggler targets `#sharedNavbar`, and these exact links each appear once:

```python
DESTINATIONS = [
    "/hub", "/", "/full", "/report", "/report_explorer",
    "/apex_uploader", "/apex_requester", "/pickup-uploader", "/dwr-uploader",
]

def render_nav(app, path):
    with app.test_request_context(path):
        return render_template("_navbar.html")
```

- [ ] **Step 2: Verify structure RED**

Run: `python -m pytest tests/test_shared_navbar.py -q`

Expected: `TemplateNotFound: _navbar.html`.

- [ ] **Step 3: Implement grouped partial**

Create one Bootstrap navbar with brand, toggler, collapse container `id="sharedNavbar"`, and six dropdown `<li>` elements. Each toggle has a unique ID, `data-bs-toggle="dropdown"`, `aria-expanded="false"`, and a `<ul class="dropdown-menu" aria-labelledby="...">`. Use the exact menu mapping approved in the design.

- [ ] **Step 4: Write failing active-state tests**

Render representative paths and assert active group/submenu pairs:

```python
@pytest.mark.parametrize(("path", "group", "item"), [
    ("/", "Master Data DS", "Performance"),
    ("/full", "Master Data DS", "Full Data"),
    ("/report", "Master Data DS", "Report"),
    ("/report_explorer", "Report Explorer", "Report Explorer"),
    ("/hub/thread/1", "Hub", "Hub"),
    ("/apex_requester", "APEX", "Apex Requester"),
    ("/pickup-uploader", "Pickup", "Pickup Uploader"),
    ("/dwr-uploader", "DWR", "DWR Uploader"),
])
def test_active_state(path, group, item):
    html = render_nav(app, path)
    assert_active_toggle(html, group)
    assert_active_item(html, item)
```

Also assert `/report` does not activate Report Explorer.

- [ ] **Step 5: Implement active-state expressions**

At the top of the partial derive booleans with Jinja `{% set %}`. Use exact match for `/`, `/full`, and `/report`; prefix match for `/hub`, `/report_explorer`, `/apex_`, `/pickup-uploader`, and `/dwr-uploader`. Apply `btn-active` to the active toggle and `active` plus `aria-current="page"` to the active submenu anchor.

- [ ] **Step 6: Verify partial GREEN**

Run: `python -m pytest tests/test_shared_navbar.py -q`

Expected: all structure and active-state tests pass.

- [ ] **Step 7: Commit shared partial**

```powershell
git add templates/_navbar.html tests/test_shared_navbar.py
git commit -m "feat: add shared grouped navbar"
```

---

### Task 2: Migrate all navbar pages to the shared partial

**Files:**
- Modify: `templates/index.html`
- Modify: `templates/report_viewer.html`
- Modify: `templates/apex_uploader.html`
- Modify: `templates/apex_requester.html`
- Modify: `templates/pickup_uploader.html`
- Modify: `templates/dwr_uploader.html`
- Modify: `templates/threads.html`
- Modify: `templates/thread_detail.html`
- Modify: `tests/test_shared_navbar.py`

**Interfaces:**
- Consumes: `templates/_navbar.html` from Task 1.
- Produces: eight pages with identical navigation markup at render time.

- [ ] **Step 1: Write failing migration test**

Read all eight templates as text and assert each contains exactly one `{% include "_navbar.html" %}` and contains no `<nav` or `</nav>` tags:

```python
PAGE_TEMPLATES = [
    "index.html", "report_viewer.html", "apex_uploader.html",
    "apex_requester.html", "pickup_uploader.html", "dwr_uploader.html",
    "threads.html", "thread_detail.html",
]
```

- [ ] **Step 2: Verify migration RED**

Run: `python -m pytest tests/test_shared_navbar.py::test_pages_include_only_shared_navbar -q`

Expected: failure listing templates that still contain local `<nav>` markup.

- [ ] **Step 3: Replace navbar blocks mechanically**

For each listed template, replace the complete `<nav ...>...</nav>` block with `{% include "_navbar.html" %}` at the same location. Do not change surrounding whitespace-sensitive scripts or page markup.

- [ ] **Step 4: Verify migration GREEN**

Run: `python -m pytest tests/test_shared_navbar.py -q`.

Expected: all shared-navbar tests pass.

- [ ] **Step 5: Run complete web regression suite**

Run: `python -m pytest -q`.

Expected: shared-navbar and DWR uploader tests all pass.

- [ ] **Step 6: Render representative pages**

Load `app.pyw` without starting Waitress, use Flask's test client for `/`, `/hub`, `/report_explorer`, `/pickup-uploader`, and `/dwr-uploader`, and assert every successful response contains `id="sharedNavbar"`. If a route requires production state and does not return 200, render its template directly under the corresponding request context.

- [ ] **Step 7: Commit migration**

```powershell
git add templates/index.html templates/report_viewer.html templates/apex_uploader.html templates/apex_requester.html templates/pickup_uploader.html templates/dwr_uploader.html templates/threads.html templates/thread_detail.html tests/test_shared_navbar.py
git commit -m "refactor: reuse shared navbar across pages"
```

- [ ] **Step 8: Final scope check**

Run `git status --short`, `git diff --check HEAD`, and `git log --oneline -5`. Confirm no unrelated files changed and the complete test suite remains green.

