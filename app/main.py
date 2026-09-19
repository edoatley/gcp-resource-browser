"""Entry point.

Kept so `python -m app.main` and the `gcp-explorer` console script both work.
The implementation lives in `app.cli`, `app.api`, and `app.core`.
"""

from app.cli import cli

if __name__ == "__main__":
    cli()
