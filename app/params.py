"""The shape of a search, and how to describe it to a human.

Single authoritative home for two things the CLI and the API would otherwise
each restate:

- `SearchFilters`, the set of inputs a search takes. Both surfaces build one and
  hand it to the core, so adding a filter changes one signature rather than
  every forwarding call.
- `Help`, the prose describing each filter. The frameworks render it very
  differently -- FastAPI into an OpenAPI schema, Typer into `--help` -- but the
  *knowledge* is the same, and keeping two copies let them drift once already.

What deliberately does NOT live here: anything true of only one surface. Short
flags (`-t`), OpenAPI `examples`, and validator spellings (`ge=1` vs `min=1`)
belong in the surface that uses them. Each surface is free to append to a Help
string where it has something extra to say.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# Results per upstream page. CAI caps this at 500 server-side regardless of what
# is asked, so this sits at the ceiling. Tuning knob, not a cap on totals.
DEFAULT_PAGE_SIZE = 500

# Default ceiling on total results, so an unqualified org-wide search cannot
# stream an unbounded set into memory. When it bites, the result is flagged
# `truncated` rather than silently shortened.
DEFAULT_LIMIT = 1000


class Help:
    """Descriptions shared by both surfaces."""

    SCOPE = "CAI scope: organizations/<id>, folders/<id>, or projects/<id>"
    TYPE = "Resource type, repeatable. A friendly name, a raw CAI asset type, or an RE2 pattern."
    TERM = "Free-text term, matched across all searchable fields"
    LABEL = "Label filter, repeatable: `key=value`, or `key` to match any value"
    LOCATION = "Location filter, repeatable; several are ORed. Supports `*` wildcards."
    PROJECT = (
        "Project filter, repeatable; several are ORed. Accepts a project ID or number "
        "(IDs are resolved to numbers, which is what CAI matches on)."
    )
    RAW_QUERY = "Raw CAI query syntax, ANDed with the other filters"
    LIMIT = "Maximum resources to return"


@dataclass(frozen=True)
class SearchFilters:
    """Everything a resource search is asked for.

    Frozen so a surface cannot mutate a request after building it, which keeps
    the query echoed back in the response honest about what was actually sent.
    """

    scope: str
    resource_types: Sequence[str]
    free_text: str = ""
    labels: Sequence[str] = field(default_factory=tuple)
    locations: Sequence[str] = field(default_factory=tuple)
    projects: Sequence[str] = field(default_factory=tuple)
    raw_query: str = ""
    limit: int | None = DEFAULT_LIMIT
    page_size: int = DEFAULT_PAGE_SIZE
