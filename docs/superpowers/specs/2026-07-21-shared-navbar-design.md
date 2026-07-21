# Shared Grouped Navbar Design

## Objective

Replace duplicated navbar markup across all performance application pages with one reusable Jinja partial. Group related destinations into dropdown menus so navigation remains consistent and compact as features grow.

## Shared Component

Create `templates/_navbar.html` as the only navbar definition. Every full page template that currently contains `<nav class="navbar ...">` replaces that block with:

```jinja2
{% include "_navbar.html" %}
```

The refactor is limited to the navbar. Page-specific `<head>`, content, scripts, footer, and layout remain unchanged. Base-template inheritance is intentionally out of scope.

## Menu Structure

The brand links to `/` and retains the JNE logo and `CCC DS Data Hub` label.

The collapsed navigation contains six top-level Bootstrap dropdowns:

- **Hub**
  - Hub → `/hub`
- **Master Data DS**
  - Performance → `/`
  - Full Data → `/full`
  - Report → `/report`
- **Report Explorer**
  - Report Explorer → `/report_explorer`
- **APEX**
  - Apex Uploader → `/apex_uploader`
  - Apex Requester → `/apex_requester`
- **Pickup**
  - Pickup Uploader → `/pickup-uploader`
- **DWR**
  - DWR Uploader → `/dwr-uploader`

Single-item dropdowns are retained deliberately so each data source has a stable top-level home for future submenu additions.

## Active State

The partial derives active state from Flask's `request.path`; page templates do not pass an active-menu variable.

- `/` activates Master Data DS and Performance only.
- `/full` activates Master Data DS and Full Data.
- `/report` activates Master Data DS and Report, but `/report_explorer` activates Report Explorer instead.
- `/hub` and hub detail routes activate Hub.
- `/apex_uploader` and `/apex_requester` activate APEX and their respective submenu.
- `/pickup-uploader` activates Pickup.
- `/dwr-uploader` activates DWR.

Active top-level toggles use the existing `btn-active` styling. Active submenu links use Bootstrap's `active` class and `aria-current="page"`.

## Responsive and Interaction Behavior

Use the existing Bootstrap navbar collapse and toggler. Dropdown toggles use `data-bs-toggle="dropdown"`, `aria-expanded="false"`, and unique IDs/labels. The shared partial does not add custom JavaScript; every page continues loading Bootstrap's JavaScript bundle through its existing scripts.

The menu must remain keyboard navigable and use buttons/anchors with valid ARIA relationships.

## Template Scope

Replace navbar blocks in:

- `templates/index.html`
- `templates/report_viewer.html`
- `templates/apex_uploader.html`
- `templates/apex_requester.html`
- `templates/pickup_uploader.html`
- `templates/dwr_uploader.html`
- `templates/threads.html`
- `templates/thread_detail.html`

Templates without a navbar are not changed.

## Testing

Add tests that render the partial under representative request paths and assert:

- all six top-level groups exist;
- every existing destination appears exactly once;
- correct top-level and submenu active states are rendered;
- `/report` does not incorrectly activate Report Explorer;
- dropdown and collapse Bootstrap attributes are present;
- all eight page templates include `_navbar.html` and no longer define a `<nav>` element themselves.

Run the complete web test suite after refactoring to protect DWR uploader behavior.

## Success Criteria

- Navbar markup has one source of truth.
- Every current navigation destination remains reachable.
- All eight pages display identical grouped menus.
- Active state follows the current Flask route automatically.
- Desktop and mobile navigation use valid Bootstrap collapse/dropdown structures.
- No page-specific content or behavior changes outside navigation.
