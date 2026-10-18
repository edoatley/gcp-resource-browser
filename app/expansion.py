"""Who is behind a member that stands for other principals.

Some IAM members are not one principal. `group:ops@x.com` is everyone in the
group; `projectOwner:p` is whoever holds Owner on project p. A grant to either
reaches all of them, and for an over-permissioning review the headcount is
usually the finding. This reads who they are:

- `group:` -- Cloud Identity, nested groups flattened (`core.group_members`).
- `projectOwner:` / `projectEditor:` / `projectViewer:` -- that project's own
  attached policy, via CAI (`core.project_role_members`).
- `principalSet:`, `domain:`, `allUsers`, `allAuthenticatedUsers` -- cannot be
  listed, so they are explained in place instead.

Each distinct member is read once, concurrently and bounded, like `fanout`.
A member that cannot be read is recorded with its error rather than failing
the search: one unreadable group should not lose the rest of the answer, and
the coverage note names it so the gap is not silent.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from app import core
from app.models import MemberExpansion
from app.role_risk import member_type

# Concurrent lookups in flight; the same ceiling, for the same reasons, as
# `fanout.DEFAULT_MAX_CONCURRENCY`.
DEFAULT_MAX_CONCURRENCY = 8

PROJECT_ROLES = {
    "projectOwner": "roles/owner",
    "projectEditor": "roles/editor",
    "projectViewer": "roles/viewer",
}

_POOL = re.compile(
    r"^principal(?:Set)?://iam\.googleapis\.com/projects/(?P<project>[^/]+)/locations/[^/]+/"
    r"workloadIdentityPools/(?P<pool>[^/]+)/(?P<rest>.+)$"
)


def explain(member: str) -> str | None:
    """What a member that cannot be listed stands for, or None if it is one principal."""
    kind = member_type(member)
    if kind == "allUsers":
        return "Anyone on the internet, signed in or not."
    if kind == "allAuthenticatedUsers":
        return "Anyone signed in to any Google account, including personal accounts."
    if kind == "domain":
        return f"Every account in the Google Workspace or Cloud Identity domain {member[7:]}."
    if kind == "principalSet":
        match = _POOL.match(member)
        if match is None:
            return (
                "A set of federated identities. They are issued outside Google and cannot be "
                "listed."
            )
        pool, project, rest = match["pool"], match["project"], match["rest"]
        where = f"workload identity pool {pool!r} (project {project})"
        if rest == "*":
            who = f"Every identity from {where}"
        elif rest.startswith("attribute."):
            attribute, _, value = rest.removeprefix("attribute.").partition("/")
            who = f"Any identity from {where} whose {attribute} is {value!r}"
        elif rest.startswith("group/"):
            who = f"Any identity from {where} in the external group {rest[6:]!r}"
        else:
            who = f"A set of identities from {where} ({rest})"
        return (
            f"{who}. These are federated identities, issued by the pool's provider, and "
            "cannot be listed; review the provider's attribute mapping and condition instead."
        )
    return None


def short_label(member: str) -> str | None:
    """A readable label for a federated member, e.g. `github-pool: repository = org/app`.

    For display only, and lossy: it drops the pool's project and location. Any
    surface showing it must show the full member somewhere too.
    """
    match = _POOL.match(member)
    if match is None:
        return None
    pool, rest = match["pool"], match["rest"]
    if rest == "*":
        return f"{pool}: every identity"
    for prefix in ("attribute.", "group/", "subject/"):
        if rest.startswith(prefix):
            key, _, value = rest.removeprefix(prefix).partition("/")
            if prefix != "attribute.":
                key, value = prefix.rstrip("/"), rest.removeprefix(prefix)
            return f"{pool}: {key} = {value}"
    return f"{pool}: {rest}"


def _lookup(member: str, client, session) -> Callable[[], list[str]] | None:
    kind, _, target = member.partition(":")
    if kind == "group":
        return lambda: core.group_members(target, session=session)
    if kind in PROJECT_ROLES:
        return lambda: core.project_role_members(target, PROJECT_ROLES[kind], client=client)
    return None


def expand_members(
    members: Iterable[str],
    client=None,
    session=None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> dict[str, MemberExpansion]:
    """Expansions keyed by member, for each distinct member that stands for others.

    Single principals (`user:`, `serviceAccount:`, ...) are left out: there is
    nobody behind them to find.
    """
    found: dict[str, MemberExpansion] = {}
    lookups: dict[str, Callable[[], list[str]]] = {}
    for member in sorted(set(members)):
        kind = member_type(member)
        lookup = _lookup(member, client, session)
        if lookup is not None:
            lookups[member] = lookup
        elif (note := explain(member)) is not None:
            found[member] = MemberExpansion(kind=kind, note=note)

    if lookups:
        with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as pool:
            futures = {member: pool.submit(lookup) for member, lookup in lookups.items()}
        for member, future in futures.items():
            kind = member_type(member)
            try:
                listed = future.result()
            except core.ResourceExplorerError as exc:
                found[member] = MemberExpansion(kind=kind, error=str(exc))
            else:
                found[member] = MemberExpansion(kind=kind, members=listed, count=len(listed))
    return dict(sorted(found.items()))
