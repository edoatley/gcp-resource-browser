"""The console entry point: a bad site config is an error message, not a traceback.

`app.core` reads the config while it is being imported, so the failure happens
before the CLI exists. These run the entry point in a fresh interpreter, since
this test process imported `app.core` long ago.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from app import main as entry
from app.config import CONFIG_ENV_VAR, ConfigError

BAD_CONFIGS = {
    "unknown key": {"nope": 1},
    "unconfirmed downgrade": {
        "role_risk_rules": [
            {"permission": "iam.serviceAccounts.actAs", "risk": "medium", "reason": "x"}
        ]
    },
}


def _run(tmp_path, config: dict | None, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("FORCE_COLOR", CONFIG_ENV_VAR)}
    env["HOME"] = str(tmp_path)  # keep the developer's own config out
    if config is not None:
        path = tmp_path / "site.json"
        path.write_text(json.dumps(config))
        env[CONFIG_ENV_VAR] = str(path)
    return subprocess.run(
        [sys.executable, "-m", "app.main", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("config", BAD_CONFIGS.values(), ids=BAD_CONFIGS.keys())
def test_a_bad_config_is_a_clean_error(tmp_path, config) -> None:
    result = _run(tmp_path, config, "types")

    assert result.returncode == entry.EXIT_BAD_CONFIG
    assert result.stderr.startswith("Error: invalid site configuration.")
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_the_message_says_how_to_fix_a_downgrade(tmp_path) -> None:
    result = _run(tmp_path, BAD_CONFIGS["unconfirmed downgrade"], "types")
    assert '"downgrade": true' in result.stderr


def test_without_a_config_the_cli_runs(tmp_path) -> None:
    result = _run(tmp_path, None, "types")
    assert result.returncode == 0, result.stderr
    assert "bucket" in result.stdout


def test_a_config_error_raised_during_a_command_is_caught_too(monkeypatch, capsys) -> None:
    """Config is also read lazily (risk rules, noise rules), not only at import."""
    import app.cli

    def failing_cli():
        raise ConfigError("site.json: broken")

    monkeypatch.setattr(app.cli, "cli", failing_cli)

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == entry.EXIT_BAD_CONFIG
    assert "invalid site configuration. site.json: broken" in capsys.readouterr().err
