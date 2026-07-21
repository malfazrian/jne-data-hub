import re
from pathlib import Path

import pytest
from flask import Flask, render_template


TEMPLATE_ROOT = Path(__file__).parents[1] / "templates"
GROUPS = ["Hub", "Master Data DS", "Report Explorer", "APEX", "Pickup", "DWR"]
DIRECT_GROUPS = {
    "Hub": "/hub",
    "Report Explorer": "/report_explorer",
    "Pickup": "/pickup-uploader",
    "DWR": "/dwr-uploader",
}
ITEMS = {
    "Hub": "/hub",
    "Performance": "/",
    "Full Data": "/full",
    "Report": "/report",
    "Report Explorer": "/report_explorer",
    "Apex Uploader": "/apex_uploader",
    "Apex Requester": "/apex_requester",
    "Pickup Uploader": "/pickup-uploader",
    "DWR Uploader": "/dwr-uploader",
}
PAGE_TEMPLATES = [
    "index.html",
    "report_viewer.html",
    "apex_uploader.html",
    "apex_requester.html",
    "pickup_uploader.html",
    "dwr_uploader.html",
    "threads.html",
    "thread_detail.html",
]


@pytest.fixture
def app():
    return Flask(__name__, template_folder=str(TEMPLATE_ROOT))


def render_nav(app, path):
    with app.test_request_context(path):
        return render_template("_navbar.html")


def test_shared_navbar_has_grouped_destinations_and_collapse(app):
    html = render_nav(app, "/")
    assert 'data-bs-target="#sharedNavbar"' in html
    assert 'id="sharedNavbar"' in html
    for group in GROUPS:
        assert html.count(f'data-nav-group="{group}"') == 1
    for item, href in ITEMS.items():
        pattern = rf'<a[^>]*data-nav-item="{re.escape(item)}"[^>]*href="{re.escape(href)}"'
        assert len(re.findall(pattern, html)) == 1
    assert html.count('data-bs-toggle="dropdown"') == 2


def test_single_destination_groups_are_direct_links(app):
    html = render_nav(app, "/")
    for group, href in DIRECT_GROUPS.items():
        tag = re.search(rf'<a[^>]*data-nav-group="{re.escape(group)}"[^>]*>', html).group()
        assert f'href="{href}"' in tag
        assert 'dropdown-toggle' not in tag
        assert 'data-bs-toggle="dropdown"' not in tag


def test_navbar_css_has_unambiguous_active_and_dropdown_states():
    css = (TEMPLATE_ROOT.parents[0] / "static" / "style.css").read_text(encoding="utf-8")
    assert ".navbar .nav-menu-link.btn-active::before" in css
    assert ".navbar .dropdown-item:hover" in css
    assert ".navbar .dropdown-item:focus" in css
    assert ".navbar .dropdown-item.active" in css


@pytest.mark.parametrize(
    ("path", "group", "item"),
    [
        ("/", "Master Data DS", "Performance"),
        ("/full", "Master Data DS", "Full Data"),
        ("/report", "Master Data DS", "Report"),
        ("/report_explorer", "Report Explorer", "Report Explorer"),
        ("/hub/thread/1", "Hub", "Hub"),
        ("/apex_requester", "APEX", "Apex Requester"),
        ("/pickup-uploader", "Pickup", "Pickup Uploader"),
        ("/dwr-uploader", "DWR", "DWR Uploader"),
    ],
)
def test_route_activates_expected_group_and_item(app, path, group, item):
    html = render_nav(app, path)
    group_tag = re.search(rf'<a[^>]*data-nav-group="{re.escape(group)}"[^>]*>', html).group()
    item_tag = re.search(rf'<a[^>]*data-nav-item="{re.escape(item)}"[^>]*>', html).group()
    assert "btn-active" in group_tag
    assert re.search(r'class="[^"]*\bactive\b', item_tag)
    assert 'aria-current="page"' in item_tag


def test_report_does_not_activate_report_explorer(app):
    html = render_nav(app, "/report")
    tag = re.search(r'<a[^>]*data-nav-group="Report Explorer"[^>]*>', html).group()
    assert "btn-active" not in tag


def test_pages_include_only_shared_navbar():
    include = '{% include "_navbar.html" %}'
    for name in PAGE_TEMPLATES:
        source = (TEMPLATE_ROOT / name).read_text(encoding="utf-8")
        assert source.count(include) == 1, name
        assert "<nav" not in source, name
        assert "</nav>" not in source, name


def test_shared_navbar_owns_single_bootstrap_bundle_dependency():
    navbar = (TEMPLATE_ROOT / "_navbar.html").read_text(encoding="utf-8")
    assert navbar.count("bootstrap.bundle.min.js") == 1
    for name in PAGE_TEMPLATES:
        source = (TEMPLATE_ROOT / name).read_text(encoding="utf-8")
        assert "bootstrap.bundle.min.js" not in source, name
