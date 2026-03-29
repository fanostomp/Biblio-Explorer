import pytest

from tests.test_api import client


def test_base_template_exposes_mobile_nav_toggle(client):
    response = client.get("/charts")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="navMenuToggle"' in html


def test_charts_page_renders_comparison_command_bar(client):
    response = client.get("/charts")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="comparisonControls"' in html


def test_base_template_exposes_collapsible_nav_container(client):
    response = client.get("/charts")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-nav-menu' in html


def test_charts_page_exposes_comparison_warning_region(client):
    response = client.get("/charts")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="comparisonWarnings"' in html


@pytest.mark.parametrize("page_path", ["/conference", "/journal"])
def test_page_exposes_year_filter_reset_button(client, page_path):
    response = client.get(page_path)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="resetFilters"' in html
