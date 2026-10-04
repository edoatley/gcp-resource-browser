# Development

```bash
uv sync                          # includes the dev group (pytest, ruff)
uv run pytest                    # no network or credentials needed
uv run ruff check .
uv run ruff format .
uv run gcpe --help
uv run python -m app.main --help # equivalent, without the console script
```

The test suite replaces the CAI client with a fake (`tests/conftest.py::FakeAssetClient`)
throughout, so it runs anywhere. The fake records every request it receives, so tests check
**what was sent to CAI** as well as what was rendered.

Read [Architecture](ARCHITECTURE.md) before changing `app/query.py`, noise reduction, or either
surface's signature.

## Generated files

Two files are generated from the code and committed. CI fails if either is out of date:

| File | Regenerate with | Changes when |
|:---|:---|:---|
| `openapi.yml` | `uv run gcpe openapi --out openapi.yml` | Any API parameter, model or description changes |
| `docs/CLI_REFERENCE.md` | `uv run typer app.cli utils docs --name gcpe --title "CLI reference" --output docs/CLI_REFERENCE.md` | Any CLI command, flag or help text changes |

Both contain the `Help` strings from `app/params.py`, so editing a shared description changes
both.

## CI

`.github/workflows/ci.yml` runs on every push to `main` and every pull request:

1. `ruff check` and `ruff format --check`
2. `pytest`
3. Checks that `openapi.yml` and `docs/CLI_REFERENCE.md` are up to date
4. Builds the image, runs `--help` in it, and checks that no credential-shaped file is in it

`.github/workflows/pages.yml` publishes `openapi.yml` with Swagger UI (`docs/api/index.html`)
to GitHub Pages whenever either changes on `main`. It needs Pages enabled with **Source: GitHub
Actions** (Settings → Pages).

## Checking against `gcloud`

A fake client answers any query it is given, so unit tests cannot catch a *wrong* query: one
that is valid but quietly misses rows. `scripts/` contains independent `gcloud`
implementations and a differential checker for that:

```bash
./scripts/compare-resources.sh --scope projects/my-project --type bucket

./scripts/compare-resources.sh --scope organizations/123 \
    --type bucket --type vm --label env=prod --location europe-west2

./scripts/check-noise.sh projects/my-project   # suppressed result ⊂ full result
```

`compare-resources.sh` diffs the tool's output against `gcloud asset search-all-resources` and
exits non-zero if they disagree. The `gcloud` side builds its query with its own logic, because
comparing the tool's query compiler against itself would prove nothing. **Every new filter
should have an equivalent there.** See [scripts/README.md](../scripts/README.md).

For manual end-to-end verification against a real estate, follow the
[walkthrough](WALKTHROUGH.md).

## Benchmarking

```bash
uv run python -m scripts.benchmark --scope organizations/<id> --repeats 3
```

This prints a markdown table ready to paste into the [delivery plan](DELIVERY_PLAN.md). Run it
against a large estate. On a small one everything fits in a single 500-row CAI page, which
makes `limit` and streaming look pointless when they are not.

## Packaging notes

- The code lives in `app/`, which doesn't match the project name, so `pyproject.toml` declares
  `[tool.hatch.build.targets.wheel] packages = ["app"]`. Without it, `uv sync` fails while
  building the wheel.
- The Dockerfile builds at `/app` rather than `/build` on purpose. A virtualenv's entry-point
  scripts contain an absolute path to the interpreter, so moving `.venv` breaks every console
  script.

## Keeping the docs current

- Update the status note at the top of `README.md` and the checkboxes in the delivery plan as
  phases are completed.
- When a filter or endpoint changes, regenerate the two generated files and update the
  hand-written tables in [API](API.md), [Concepts](CONCEPTS.md) and [CLI guide](CLI.md).
