from pathlib import Path


TEMPLATE = Path(__file__).parents[1] / "templates" / "report_viewer.html"


def test_report_cards_use_compact_file_library_structure():
    source = TEMPLATE.read_text(encoding="utf-8")

    for class_name in (
        "file-card-header",
        "file-type-tile",
        "file-card-actions",
        "file-card-details",
        "file-meta-row",
        "file-card-footer",
    ):
        assert class_name in source

    assert "download-single" in source
    assert "file-checkbox" in source
    assert "star-btn" in source


def test_report_card_filename_supports_two_lines_and_clear_states():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "-webkit-line-clamp: 2" in source
    assert ".file-card:hover" in source
    assert ".file-card.selected" in source
    assert ".file-card:focus-within" in source
    assert ".file-type-tile" in source
    assert ".file-card-footer" in source


def test_report_list_renders_in_progressive_batches():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "const REPORT_BATCH_SIZE = 60" in source
    assert "currentFilteredFiles.slice(0, visibleReportLimit)" in source
    assert 'id="loadMoreReportsBtn"' in source
    assert 'id="reportRenderCount"' in source
    assert "DocumentFragment" in source


def test_report_search_is_debounced_and_preferences_load_once():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "const SEARCH_DEBOUNCE_MS = 250" in source
    assert "clearTimeout(searchDebounceTimer)" in source
    assert "setTimeout(filterAndRenderFiles, SEARCH_DEBOUNCE_MS)" in source
    assert source.count("await loadUserPreferences()") == 1


def test_select_all_still_targets_every_filtered_report():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "currentFilteredFiles.forEach(file =>" in source
    assert "selectedFiles.add(file.rel_path || file.name)" in source


def test_industry_filter_is_single_row_before_reports_finish_loading():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "#tagSelect:not(.tomselected)" in source
    init_position = source.index("renderTagSelect([])")
    load_position = source.index("await loadFiles()")
    assert init_position < load_position


def test_bulk_controls_use_explicit_selection_mode():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="selectionModeBtn"' in source
    assert 'id="cancelSelectionBtn"' in source
    assert "let selectionMode = false" in source
    assert "function setSelectionMode(enabled" in source
    assert "selection-mode-active" in source
    assert "if (!selectionMode) return;" in source
    assert ".file-checkbox" in source


def test_all_and_favorite_tabs_are_mutually_exclusive_views():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="allFilesCount"' in source
    assert 'id="favoriteFilesCount"' in source
    assert 'id="reportListTitle"' in source
    assert 'id="favoriteList"' not in source
    assert "function updateFilterCounts()" in source
    assert "return b.modified_ts - a.modified_ts" in source
