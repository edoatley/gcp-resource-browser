"""Guardrails on the role risk ruleset itself (Phase 10).

A rule matches an exact permission string, so a rule that is wrong in the
quiet way -- a typo, a retired permission, a site override that lowers a
level -- does not fail: it just stops reporting grants. These tests make each
of those a failure instead. `serviceusage.apiKeys.create` shipped as a dead
rule until the first of them ran.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from app import config, core, role_risk
from app.params import BillingGrantFilters, GrantFilters
from tests.conftest import BILLING_ACCOUNTS_URL, FakeAssetClient, FakeResponse, catalogue

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "iam_permissions.txt.gz"
BUILTIN = json.loads(role_risk.RULES_FILE.read_text())


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """No developer config leaks in; caches start and end empty."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(config.CONFIG_ENV_VAR, raising=False)
    config.load_config.cache_clear()
    role_risk.load_rules.cache_clear()
    core._ROLE_CATALOGUE.clear()
    yield
    config.load_config.cache_clear()
    role_risk.load_rules.cache_clear()
    core._ROLE_CATALOGUE.clear()


def _site(tmp_path, *rules: dict) -> None:
    (tmp_path / config.CONFIG_FILENAME).write_text(json.dumps({"role_risk_rules": list(rules)}))


# --- Every rule names a real permission -------------------------------------------


def _snapshot() -> set[str]:
    with gzip.open(SNAPSHOT, "rt") as handle:
        return {line.strip() for line in handle if line.strip() and not line.startswith("#")}


def test_the_snapshot_is_a_real_catalogue() -> None:
    with gzip.open(SNAPSHOT, "rt") as handle:
        header = handle.readline()
    assert header.startswith("# Every permission in a predefined IAM role")
    assert len(_snapshot()) > 10_000


@pytest.mark.parametrize("rule", BUILTIN["rules"], ids=lambda r: r["permission"])
def test_every_rule_names_a_permission_that_exists(rule) -> None:
    """Refresh with `uv run python -m scripts._snapshot_permissions` for a newer permission."""
    assert rule["permission"] in _snapshot(), (
        f"{rule['permission']} is in no predefined role: a typo, or retired. "
        "scripts/check-rule-permissions.sh checks the live catalogue."
    )


# --- Every source is used, and every citation resolves -------------------------------


def _anchor(source_id: str) -> str:
    return re.sub(r"[^\w\- ]", "", source_id.lower())


def test_every_source_is_cited_by_a_rule_or_the_evidence_page() -> None:
    """A source nothing cites is dead weight that reads as support for something."""
    page = (ROOT / "docs" / "ROLE_RISK.md").read_text()
    cited_by_rules = {s for rule in BUILTIN["rules"] for s in rule["sources"]}
    for source_id in BUILTIN["sources"]:
        linked = f"](#{_anchor(source_id)})" in page
        assert source_id in cited_by_rules or linked, f"{source_id} is cited by nothing"


# --- Site rules -----------------------------------------------------------------------


def test_a_site_rule_citing_an_unknown_source_is_rejected(tmp_path) -> None:
    _site(tmp_path, {"permission": "a.b.c", "risk": "high", "reason": "x", "sources": ["nope"]})
    with pytest.raises(config.ConfigError, match="unknown source.*nope"):
        config.load_config()


def test_a_site_rule_with_an_unknown_key_is_rejected(tmp_path) -> None:
    """`downgrades` (a typo) must not be silently ignored."""
    _site(tmp_path, {"permission": "a.b.c", "risk": "high", "reason": "x", "downgrades": True})
    with pytest.raises(config.ConfigError, match="unknown key.*downgrades"):
        config.load_config()


def test_a_site_rule_may_cite_built_in_sources(tmp_path) -> None:
    _site(
        tmp_path,
        {"permission": "a.b.c", "risk": "high", "reason": "x", "sources": ["rhino-part1"]},
    )
    assert config.load_config().extra_role_risk_rules[0]["sources"] == ["rhino-part1"]


def test_raising_a_rule_needs_no_confirmation(tmp_path) -> None:
    _site(tmp_path, {"permission": "storage.buckets.setIamPolicy", "risk": "high", "reason": "x"})
    config.load_config()
    assert role_risk.downgrade_note() == ""


def test_an_unconfirmed_downgrade_is_rejected(tmp_path) -> None:
    _site(tmp_path, {"permission": "iam.serviceAccounts.actAs", "risk": "medium", "reason": "x"})
    with pytest.raises(
        config.ConfigError, match='lowers it from high to medium.*"downgrade": true'
    ):
        config.load_config()


def test_downgrade_true_on_a_rule_that_lowers_nothing_is_rejected(tmp_path) -> None:
    _site(
        tmp_path,
        {"permission": "acme.widgets.delete", "risk": "medium", "reason": "x", "downgrade": True},
    )
    with pytest.raises(config.ConfigError, match="does not lower"):
        config.load_config()


def test_downgrade_must_be_a_boolean(tmp_path) -> None:
    _site(
        tmp_path,
        {
            "permission": "iam.serviceAccounts.actAs",
            "risk": "medium",
            "reason": "x",
            "downgrade": "yes",
        },
    )
    with pytest.raises(config.ConfigError, match="must be true or false"):
        config.load_config()


def _confirmed_downgrade(tmp_path) -> None:
    _site(
        tmp_path,
        {
            "permission": "iam.serviceAccounts.actAs",
            "risk": "medium",
            "reason": "we review deployers separately",
            "downgrade": True,
        },
    )


def test_a_confirmed_downgrade_applies_and_is_named(tmp_path) -> None:
    _confirmed_downgrade(tmp_path)

    rules = {r.permission: r for r in role_risk.load_rules()}

    assert rules["iam.serviceAccounts.actAs"].risk is role_risk.Risk.MEDIUM
    assert "iam.serviceAccounts.actAs (high -> medium)" in role_risk.downgrade_note()


def test_every_coverage_note_names_a_downgrade(tmp_path) -> None:
    _confirmed_downgrade(tmp_path)
    session = catalogue(
        ("roles/owner", ["resourcemanager.projects.setIamPolicy"]),
        ("roles/viewer", []),
        **{BILLING_ACCOUNTS_URL: FakeResponse({"billingAccounts": []})},
    )

    roles = core.list_risky_roles("high", session=session)
    grants = core.search_grants(GrantFilters(scope="projects/p"), client=FakeAssetClient())
    billing = core.search_billing_grants(BillingGrantFilters(), session=session)

    for note in (roles.coverage_note, grants.coverage_note, billing.coverage_note):
        assert "lowers 1 built-in risk rule(s)" in note
        assert "iam.serviceAccounts.actAs (high -> medium)" in note


def test_without_downgrades_the_coverage_notes_are_unchanged() -> None:
    grants = core.search_grants(GrantFilters(scope="projects/p"), client=FakeAssetClient())
    assert "lowers" not in grants.coverage_note
