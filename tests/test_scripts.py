"""Cover the differential-check helper.

It silently went stale when `search_resources` changed signature -- the code
was inlined in a bash heredoc where neither ruff nor pytest could see it, and
nothing noticed until the check was run by hand against real GCP. Now it is a
module, these tests keep it wired to the current API.
"""

from __future__ import annotations

import json

import pytest

from app import core
from scripts import _explorer_search
from tests.conftest import FakeAssetClient, make_search_result


@pytest.fixture
def use_fake(monkeypatch):
    def _use(fake: FakeAssetClient) -> FakeAssetClient:
        monkeypatch.setattr(core, "get_client", lambda: fake)
        return fake

    return _use


def test_emits_one_json_object_per_resource(use_fake, monkeypatch, capsys) -> None:
    use_fake(FakeAssetClient(results=[make_search_result(display_name="b1")]))
    monkeypatch.setattr(
        "sys.argv", ["_explorer_search", "--scope", "projects/p", "--type", "bucket"]
    )

    assert _explorer_search.main() == 0

    line = capsys.readouterr().out.strip()
    assert json.loads(line)["display_name"] == "b1"


def test_output_matches_jq_compact_formatting(use_fake, monkeypatch, capsys) -> None:
    """gcloud's side uses `jq -c`; a spacing difference would diff as a mismatch."""
    use_fake(FakeAssetClient(results=[make_search_result()]))
    monkeypatch.setattr(
        "sys.argv", ["_explorer_search", "--scope", "projects/p", "--type", "bucket"]
    )

    _explorer_search.main()

    line = capsys.readouterr().out.strip()
    assert ", " not in line
    assert ": " not in line


def test_filters_reach_the_query(use_fake, monkeypatch, capsys) -> None:
    fake = use_fake(FakeAssetClient(results=[]))
    monkeypatch.setattr(
        "sys.argv",
        [
            "_explorer_search",
            "--scope",
            "organizations/1",
            "--type",
            "bucket",
            "--label",
            "env=prod",
            "--location",
            "europe-west2",
            "--term",
            "backup",
        ],
    )

    _explorer_search.main()

    assert fake.last_request.query == "backup labels.env:prod location:europe-west2"


def test_reports_errors_without_traceback(use_fake, monkeypatch, capsys) -> None:
    use_fake(FakeAssetClient())
    monkeypatch.setattr(
        "sys.argv", ["_explorer_search", "--scope", "not-a-scope", "--type", "bucket"]
    )

    assert _explorer_search.main() == 1
    assert "explorer error" in capsys.readouterr().err
