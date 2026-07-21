# Report Explorer Card Refresh Design

## Goal

Make the report list feel like a compact professional file library while preserving every existing interaction.

## Design

- Use a compact, consistent-height card in a responsive three/two/one-column grid.
- Place the file-type icon in a tinted tile and allow the filename up to two lines.
- Keep category, size, and modified time visually secondary and aligned.
- Move selection and favorite controls into a quiet action cluster at the top right.
- Use a compact solid-blue Download button in the card footer.
- Give hover, keyboard focus, selected, favorite, and recent states distinct accessible styling.

## Scope

Only `templates/report_viewer.html` card CSS and `createFileCard()` markup change. Existing event handlers, endpoints, data fields, filtering, bulk selection, favorites, recent downloads, and responsive behavior remain intact.

## Verification

Static regression tests will assert the new card structure and state classes. The complete Python test suite must remain green.
