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
