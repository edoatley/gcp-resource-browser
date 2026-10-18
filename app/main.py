"""Entry point for the `gcpe` / `gcp-explorer` console scripts and `python -m app.main`.

It exists to catch one thing the CLI itself cannot: a bad site config. `app.core`
reads the config at import (the asset-type mapping is built from it), so an
invalid file fails while `app.cli` is still being imported -- before Typer, or
the CLI's own error-to-exit-code mapping, is running. Without this, a typo in
`gcp-explorer.json` reached the user as a Python traceback.
"""

from __future__ import annotations

import sys

from app.config import ConfigError

# Distinct from the CLI's codes (see `app.cli`): the site config file is
# invalid, so nothing can run until it is fixed.
EXIT_BAD_CONFIG = 8


def main() -> None:
    try:
        from app.cli import cli  # imported here: importing it reads the config

        cli()
    except ConfigError as exc:
        # Plain stderr, not rich: the CLI module may be the thing that failed.
        print(f"Error: invalid site configuration. {exc}", file=sys.stderr)
        sys.exit(EXIT_BAD_CONFIG)


if __name__ == "__main__":
    main()
