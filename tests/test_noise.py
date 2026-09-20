"""Noise reduction.

Hiding rows from an audit tool is the riskiest thing here, so the invariants
matter more than the rules: --show-all always disables suppression, and what
was hidden is always reported.

Suppression is client-side by necessity, not preference. Verified against the
live API that `assetType` is not a queryable CAI field (400 Unsupported field),
and `asset_types` is an include list with no exclusion -- RE2 has no negative
lookahead.
"""

from __future__ import annotations

import pytest

from app import core
from app.models import Resource
from app.noise import NoiseFilter, load_rules
from app.params import SearchFilters
from tests.conftest import FakeAssetClient, filters, make_search_result


def resource(asset_type: str, name: str = "thing") -> Resource:
    return Resource(full_name=f"//x/{name}", asset_type=asset_type, display_name=name)


def test_rules_load_and_carry_reasons() -> None:
    rules = load_rules()
    assert rules
    assert all(rule.reason for rule in rules), "every rule must explain itself"


def test_whole_type_suppression() -> None:
    nf = NoiseFilter()
    assert not nf.keep(resource("serviceusage.googleapis.com/Service", "storage-api"))
    assert nf.keep(resource("storage.googleapis.com/Bucket", "my-bucket"))


@pytest.mark.parametrize(
    ("asset_type", "noisy_name", "real_name"),
    [
        ("compute.googleapis.com/Route", "default-route-abc123", "my-custom-route"),
        ("compute.googleapis.com/Subnetwork", "default", "prod-subnet"),
        ("compute.googleapis.com/Network", "default", "prod-vpc"),
        ("compute.googleapis.com/Firewall", "default-allow-internal", "allow-prod-https"),
        ("logging.googleapis.com/LogSink", "_Required", "security-export"),
        ("logging.googleapis.com/LogBucket", "_Default", "audit-retention"),
        ("dataplex.googleapis.com/EntryGroup", "@storage", "my-entries"),
    ],
)
def test_name_scoped_rules_keep_user_created_resources(
    asset_type: str, noisy_name: str, real_name: str
) -> None:
    """The point of name-scoped rules: hide the auto-created, keep the deliberate."""
    assert not NoiseFilter().keep(resource(asset_type, noisy_name))
    assert NoiseFilter().keep(resource(asset_type, real_name))


def test_show_all_disables_every_rule() -> None:
    nf = NoiseFilter(enabled=False)
    assert nf.keep(resource("serviceusage.googleapis.com/Service"))
    assert nf.suppressed == 0
    assert nf.summary() == ""


def test_summary_names_what_was_hidden() -> None:
    nf = NoiseFilter()
    for _ in range(3):
        nf.keep(resource("serviceusage.googleapis.com/Service"))
    summary = nf.summary()
    assert "3 hidden" in summary
    assert "enabled API services" in summary
    assert "--show-all" in summary


def test_summary_is_empty_when_nothing_was_hidden() -> None:
    nf = NoiseFilter()
    nf.keep(resource("storage.googleapis.com/Bucket"))
    assert nf.summary() == ""


# --- integration with search ------------------------------------------------


def _noisy_and_real(noisy: int = 5, real: int = 2):
    results = [
        make_search_result(
            name=f"//x/api{i}",
            asset_type="serviceusage.googleapis.com/Service",
            display_name=f"api{i}",
        )
        for i in range(noisy)
    ]
    results += [
        make_search_result(name=f"//x/b{i}", display_name=f"bucket{i}") for i in range(real)
    ]
    return results


def test_search_suppresses_and_counts() -> None:
    client = FakeAssetClient(results=_noisy_and_real())

    result = core.search_resources(filters(types=()), client=client)

    assert len(result.resources) == 2
    assert result.suppressed == 5
    assert "enabled API services" in result.suppressed_summary


def test_show_all_returns_everything() -> None:
    client = FakeAssetClient(results=_noisy_and_real())

    result = core.search_resources(
        SearchFilters(scope="projects/p", resource_types=[], show_all=True), client=client
    )

    assert len(result.resources) == 7
    assert result.suppressed == 0


def test_limit_counts_visible_results_not_fetched_ones() -> None:
    """A limit of 2 must yield 2 real resources, not 2 rows of which 2 were noise."""
    client = FakeAssetClient(results=_noisy_and_real(noisy=20, real=5))

    result = core.search_resources(filters(types=(), limit=2), client=client)

    assert len(result.resources) == 2
    assert all(r.asset_type == "storage.googleapis.com/Bucket" for r in result.resources)
    assert result.truncated is True


def test_not_truncated_when_visible_results_fit() -> None:
    client = FakeAssetClient(results=_noisy_and_real(noisy=20, real=3))

    result = core.search_resources(filters(types=(), limit=3), client=client)

    assert len(result.resources) == 3
    assert result.truncated is False
    assert result.suppressed == 20
