"""Cloud Asset Inventory query construction.

Filters are compiled into CAI query syntax and sent upstream, never applied
client-side. Filtering after the fact would mean fetching an org-wide result
set to discard most of it, which is the scaling failure this tool exists to
avoid.

CAI query syntax, as documented on SearchAllResourcesRequest:

    field:value     field contains `value` as a word (or prefix, with `*`)
    field=value     field is exactly `value`
    NOT term        negation
    a b             space-separated terms are ANDed
    field:(a OR b)  parenthesised alternation

Values are quoted defensively. A value carrying a space, a parenthesis, or a
bare `OR` would otherwise change the structure of the query rather than the
value being matched -- which in an audit tool means silently wrong results,
the failure mode that matters most here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Values made only of these characters are emitted bare, which preserves CAI's
# `*` wildcard (`--location 'europe-*'`). Anything else is quoted, and quoting
# makes `*` a literal.
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_\-./*]+$")

# A plain user label key: safe to emit bare.
_SIMPLE_LABEL_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")

# Google's own system labels are namespaced and appear throughout real estates:
# `cloud.googleapis.com/location`, `serving.knative.dev/service`,
# `run.googleapis.com/startupProbeType`. These are legitimate keys to filter on,
# but CAI rejects them unquoted -- verified against the live API:
#   labels."cloud.googleapis.com/location":us-central1   -> works
#   labels.cloud.googleapis.com/location:us-central1     -> 400 Unsupported field
_NAMESPACED_LABEL_KEY = re.compile(r"^[a-z][a-z0-9_.\-]*(/[a-zA-Z0-9_.\-]+)?$")

# A label spec is `key=value`, or bare `key` to match "has this label at all".
_LABEL_SPEC = re.compile(r"^(?P<key>[^=]+)(?:=(?P<value>.*))?$")


class QueryError(ValueError):
    """A filter could not be turned into a valid CAI query term."""


def quote(value: str) -> str:
    """Render a value safe to embed in a CAI query."""
    if value == "":
        raise QueryError("Empty filter value.")
    if any(ord(ch) < 0x20 for ch in value):
        raise QueryError(f"Filter value contains a control character: {value!r}")
    if _SAFE_VALUE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def label_term(spec: str) -> str:
    """Compile `env=prod` into `labels.env:prod`, or `env` into `labels.env:*`.

    The bare-key form asks "does this resource carry this label at all", which
    is how you find resources *missing* a required label when combined with NOT.
    """
    match = _LABEL_SPEC.match(spec)
    if match is None or not match.group("key"):
        raise QueryError(f"Invalid label filter {spec!r}. Expected key=value or key.")

    key = match.group("key").strip()
    field = _label_field(key)

    value = match.group("value")
    if value is None:
        return f"{field}:*"
    if value == "":
        raise QueryError(
            f"Label filter {spec!r} has an empty value. Use {key!r} alone to match any value."
        )
    return f"{field}:{quote(value)}"


def _label_field(key: str) -> str:
    """Render `labels.<key>`, quoting the key when CAI requires it."""
    if _SIMPLE_LABEL_KEY.match(key):
        return f"labels.{key}"
    if _NAMESPACED_LABEL_KEY.match(key):
        # Quoting is mandatory here, not cosmetic.
        return f'labels."{key}"'
    raise QueryError(
        f"Invalid label key {key!r}. Expected a label key such as `env`, or a namespaced "
        "key such as `cloud.googleapis.com/location`."
    )


def _alternation(field: str, values: Iterable[str]) -> str:
    """Build `field:v` for one value, or `field:(a OR b)` for several."""
    quoted = [quote(v) for v in values]
    if not quoted:
        raise QueryError(f"No values supplied for {field}.")
    if len(quoted) == 1:
        return f"{field}:{quoted[0]}"
    return f"{field}:({' OR '.join(quoted)})"


def build_query(
    free_text: str = "",
    labels: Iterable[str] = (),
    locations: Iterable[str] = (),
    projects: Iterable[str] = (),
    raw: str = "",
) -> str:
    """Compile filters into a single CAI query string.

    Filters of different kinds are ANDed; several values of the same kind are
    ORed. `raw` is appended verbatim for query syntax this does not model yet.
    """
    terms: list[str] = []

    if free_text.strip():
        terms.append(free_text.strip())

    terms.extend(label_term(spec) for spec in labels)

    locations = list(locations)
    if locations:
        terms.append(_alternation("location", locations))

    projects = list(projects)
    if projects:
        terms.append(_alternation("project", projects))

    if raw.strip():
        terms.append(raw.strip())

    return " ".join(terms)
