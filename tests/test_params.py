"""The CLI and API must describe the same filters the same way.

Help text drifted once already: the API documented RE2 patterns for `--type`
while the CLI did not, though both supported them. These tests make that
class of drift a failure rather than a surprise.
"""

from __future__ import annotations

import inspect

from app import api, cli
from app.params import Help

# Filters both surfaces expose, as (cli parameter, api parameter).
SHARED_FILTERS = [
    ("scope", "scope"),
    ("resource_type", "type"),
    ("label", "label"),
    ("location", "location"),
    ("project", "project"),
    ("raw_query", "raw_query"),
    ("limit", "limit"),
]


def _cli_help(param: str) -> str:
    option = inspect.signature(cli.search).parameters[param].default
    return option.help or ""


def _api_description(param: str) -> str:
    query = inspect.signature(api.search_resources).parameters[param].default
    return query.description or ""


def test_both_surfaces_expose_the_same_filters() -> None:
    cli_params = set(inspect.signature(cli.search).parameters)
    api_params = set(inspect.signature(api.search_resources).parameters)

    for cli_name, api_name in SHARED_FILTERS:
        assert cli_name in cli_params, f"CLI lost {cli_name}"
        assert api_name in api_params, f"API lost {api_name}"


def test_shared_help_text_comes_from_one_source() -> None:
    """Each surface may append, but the shared sentence must be present verbatim."""
    shared = {
        ("scope", "scope"): Help.SCOPE,
        ("resource_type", "type"): Help.TYPE,
        ("label", "label"): Help.LABEL,
        ("location", "location"): Help.LOCATION,
        ("project", "project"): Help.PROJECT,
        ("raw_query", "raw_query"): Help.RAW_QUERY,
        ("limit", "limit"): Help.LIMIT,
    }
    for (cli_name, api_name), text in shared.items():
        assert text in _cli_help(cli_name), f"CLI {cli_name} drifted from Help"
        assert text in _api_description(api_name), f"API {api_name} drifted from Help"


def test_type_help_documents_re2_on_both_surfaces() -> None:
    """The specific drift that prompted this module."""
    assert "RE2" in _cli_help("resource_type")
    assert "RE2" in _api_description("type")


def test_api_type_description_lists_the_friendly_names() -> None:
    """Surface-specific addition: the schema can afford the full list, --help cannot."""
    description = _api_description("type")
    assert "bucket" in description
    assert "vm" in description
    assert "bucket" not in _cli_help("resource_type")
