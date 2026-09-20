"""Site configuration.

One override mechanism shared by the asset-type mapping and the noise ruleset.
Merge rather than replace: a site adds what it cares about without restating
the defaults, so an upgrade cannot silently drop new ones.
"""

from __future__ import annotations

import json

import pytest

from app import config


@pytest.fixture(autouse=True)
def clear_caches(monkeypatch, tmp_path):
    """Isolate each test from the developer's own config and from caching."""
    config.load_config.cache_clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(config.CONFIG_ENV_VAR, raising=False)
    yield
    config.load_config.cache_clear()


def write(tmp_path, data: dict, name: str = config.CONFIG_FILENAME):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_no_config_is_not_an_error(tmp_path) -> None:
    cfg = config.load_config()
    assert cfg.source is None
    assert cfg.extra_asset_types == {}


def test_reads_config_from_the_working_directory(tmp_path) -> None:
    write(tmp_path, {"asset_types": {"zone": "dns.googleapis.com/ManagedZone"}})

    cfg = config.load_config()

    assert cfg.extra_asset_types == {"zone": "dns.googleapis.com/ManagedZone"}


def test_env_var_takes_precedence(tmp_path, monkeypatch) -> None:
    write(tmp_path, {"asset_types": {"a": "x.googleapis.com/A"}})
    explicit = write(tmp_path, {"asset_types": {"b": "x.googleapis.com/B"}}, "elsewhere.json")
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(explicit))

    assert config.load_config().extra_asset_types == {"b": "x.googleapis.com/B"}


def test_missing_explicit_path_is_an_error(tmp_path, monkeypatch) -> None:
    """Naming a path and having it silently ignored is worse than failing."""
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(tmp_path / "nope.json"))

    with pytest.raises(config.ConfigError, match="does not exist"):
        config.load_config()


def test_invalid_json_names_the_file(tmp_path) -> None:
    (tmp_path / config.CONFIG_FILENAME).write_text("{not json")

    with pytest.raises(config.ConfigError, match="invalid JSON"):
        config.load_config()


def test_unknown_key_is_rejected(tmp_path) -> None:
    """A typo'd key would otherwise leave someone sure their config applied."""
    write(tmp_path, {"asset_typos": {}})

    with pytest.raises(config.ConfigError, match="unknown key"):
        config.load_config()


@pytest.mark.parametrize(
    "bad",
    [
        {"asset_types": ["not", "a", "mapping"]},
        {"noise_rules": {"not": "a list"}},
        {"noise_rules": [{"asset_type": "x"}]},
        {"unsuppress": "not a list"},
    ],
)
def test_malformed_sections_are_rejected(tmp_path, bad: dict) -> None:
    write(tmp_path, bad)
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_site_types_merge_with_the_defaults(tmp_path) -> None:
    from app import core

    write(tmp_path, {"asset_types": {"zone": "dns.googleapis.com/ManagedZone"}})
    config.load_config.cache_clear()

    # Call the loader rather than reloading the module: importlib.reload would
    # rebind core's exception classes, so other tests' `except` clauses would
    # stop matching. An order-dependent failure is worse than a verbose test.
    types = core._load_asset_types()

    assert types["zone"] == "dns.googleapis.com/ManagedZone"
    assert types["bucket"] == "storage.googleapis.com/Bucket", "defaults survive"


def test_unsuppress_turns_off_a_default_rule(tmp_path) -> None:
    from app import noise

    write(tmp_path, {"unsuppress": ["serviceusage.googleapis.com/Service"]})
    noise.load_rules.cache_clear()

    types = {rule.asset_type for rule in noise.load_rules()}
    assert "serviceusage.googleapis.com/Service" not in types
    assert "compute.googleapis.com/Route" in types, "other defaults survive"

    noise.load_rules.cache_clear()


def test_site_noise_rules_are_added(tmp_path) -> None:
    from app import noise

    write(
        tmp_path,
        {"noise_rules": [{"asset_type": "acme.example.com/Widget", "reason": "internal churn"}]},
    )
    noise.load_rules.cache_clear()

    types = {rule.asset_type for rule in noise.load_rules()}
    assert "acme.example.com/Widget" in types
    assert "serviceusage.googleapis.com/Service" in types, "defaults survive"

    noise.load_rules.cache_clear()
