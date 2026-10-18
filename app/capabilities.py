"""The capability catalogue: every thing this tool can do, with runnable examples.

One answer to "what can this do?", and the source the `gcpe docs` browser
renders. It is declarative and deliberately thin: it names capabilities and
their examples, and *derives* everything else from the code that implements
them, so the catalogue cannot describe a surface that no longer exists.

- Purpose lines are the routes' own `summary`.
- Parameters are read from the route and CLI command signatures and paired by
  the `Help` string they share -- the same pairing `tests/test_params.py`
  enforces. Nothing here restates a flag name or a description.
- An example is structured data (API parameter -> value), rendered three ways:
  a `gcpe` argv, a `/v1` URL, and the equivalent `gcloud` command. Hand-typed
  strings would drift three ways; one spec cannot.

`tests/test_capabilities.py` is the staleness guard: every CLI command and
every `/v1` route must be listed here or explicitly excluded, and every example
must run cleanly on both surfaces.
"""

from __future__ import annotations

import inspect
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from urllib.parse import urlencode

from fastapi.routing import APIRoute
from typer.models import ArgumentInfo, OptionInfo

from app import core, gcloud, role_risk
from app.params import SORTABLE_FIELDS, Help
from app.query import QueryError, build_query

# Substituted with the caller's scope when an example is rendered, so the
# browser can show the logged-in session's scope rather than a placeholder.
SCOPE = "{scope}"

# Themes in display order.
THEMES = ("Search", "Aggregation", "IAM risk", "Reference")


@dataclass(frozen=True)
class Example:
    """One runnable use of a capability.

    `args` is keyed by the route's query-parameter names, the one spelling
    every surface can be derived from. A value of `SCOPE` is filled in at
    render time; lists become repeated parameters or flags.
    """

    id: str
    title: str
    args: Mapping[str, str | bool | Sequence[str]] = field(default_factory=dict)
    # Scope levels the example can run against (`projects`, ...); empty means
    # any. Lets a renderer say why an example is unavailable for a scope,
    # rather than offering a call the API is certain to reject.
    scope_levels: Sequence[str] = ()

    def unavailable_for(self, scope: str) -> str | None:
        """Why this example cannot run against `scope`, or None if it can."""
        level = scope.split("/", 1)[0]
        if self.scope_levels and level not in self.scope_levels:
            allowed = " or ".join(f"{lvl}/" for lvl in self.scope_levels)
            return f"Runs only against {allowed} scopes, not {level}/."
        return None


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    theme: str
    route: str
    # None where the capability has no CLI form (the NDJSON stream).
    cli_command: str | None
    # Where the full explanation lives; links, never restated prose.
    docs: Sequence[str]
    examples: Sequence[Example]
    # Other CLI commands that are a narrower form of this one.
    cli_aliases: Sequence[str] = ()
    # No gcloud form: `types` is this tool's own mapping.
    has_gcloud: bool = True


@dataclass(frozen=True)
class Param:
    """A capability input, paired across the two surfaces by its `Help` text."""

    api: str
    # `None` when the CLI has no equivalent; positional arguments have no flag.
    cli_flag: str | None
    cli_positional: bool
    help: str
    help_name: str | None
    is_list: bool
    is_bool: bool
    # "str", "int", "float", "bool" or "list", from the route's annotation.
    kind: str = "str"
    required: bool = False
    # Known-good values to offer, from the same sources the code validates
    # against. Not a closed list: raw asset types and RE2 patterns are valid too.
    suggestions: tuple[str, ...] = ()


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="search",
        title="Search resources",
        theme="Search",
        route="/v1/resources",
        cli_command="search",
        cli_aliases=("list-resources",),
        docs=("docs/CONCEPTS.md#filters", "docs/CLI.md#searching", "docs/API.md#get-v1resources"),
        examples=(
            Example("buckets", "Every bucket in the scope", {"scope": SCOPE, "type": ["bucket"]}),
            Example(
                "prod-vms-in-london",
                "VMs labelled env=prod in europe-west2",
                {
                    "scope": SCOPE,
                    "type": ["vm"],
                    "label": ["env=prod"],
                    "location": ["europe-west2"],
                },
            ),
            Example(
                "by-name", "Anything whose fields mention a term", {"scope": SCOPE, "q": "web"}
            ),
            Example(
                "newest-first",
                "Newest resources first",
                {"scope": SCOPE, "sort": ["createTime DESC"], "limit": "20"},
            ),
            Example(
                "buckets-with-iam",
                "Buckets with their attached IAM bindings",
                {"scope": SCOPE, "type": ["bucket"], "include_iam": True},
            ),
            Example(
                "buckets-with-effective-iam",
                "Buckets with attached and inherited IAM bindings",
                {"scope": SCOPE, "type": ["bucket"], "include_iam": True, "effective": True},
            ),
            Example(
                "show-hidden",
                "Everything, including resources hidden by default",
                {"scope": SCOPE, "show_all": True, "limit": "50"},
            ),
        ),
    ),
    Capability(
        id="stream",
        title="Stream resources",
        theme="Search",
        route="/v1/resources/stream",
        cli_command=None,
        docs=("docs/API.md#get-v1resourcesstream",),
        examples=(
            Example(
                "everything",
                "Everything in the scope, one JSON object per line",
                {"scope": SCOPE},
            ),
        ),
    ),
    Capability(
        id="summary",
        title="Summarise a scope",
        theme="Aggregation",
        route="/v1/summary",
        cli_command="summary",
        docs=("docs/CLI.md#summaries", "docs/API.md#get-v1summary"),
        examples=(
            Example("everything", "Counts by type, project and location", {"scope": SCOPE}),
            Example("prod", "Counts for env=prod only", {"scope": SCOPE, "label": ["env=prod"]}),
        ),
    ),
    Capability(
        id="roles",
        title="Risky IAM roles",
        theme="IAM risk",
        route="/v1/roles",
        cli_command="roles",
        docs=(
            "docs/CONCEPTS.md#role-risk",
            "docs/CLI.md#high-risk-roles-and-grants",
            "docs/API.md#get-v1roles",
            "docs/ROLE_RISK.md",
        ),
        examples=(
            Example("high", "Predefined roles at high risk"),
            Example("medium", "Predefined roles at medium risk or above", {"risk": "medium"}),
            # A folder scope is rejected here: custom roles cannot live on one.
            Example(
                "custom",
                "Including custom roles defined on the scope",
                {"scope": SCOPE},
                scope_levels=core.CUSTOM_ROLE_LEVELS,
            ),
        ),
    ),
    Capability(
        id="grants",
        title="Risky IAM grants",
        theme="IAM risk",
        route="/v1/grants",
        cli_command="grants",
        docs=(
            "docs/CONCEPTS.md#role-risk",
            "docs/CLI.md#high-risk-roles-and-grants",
            "docs/API.md#get-v1grants",
        ),
        examples=(
            Example("high", "Who holds a high-risk role", {"scope": SCOPE}),
            Example(
                "users-by-member",
                "Users only, rolled up by member",
                {"scope": SCOPE, "member_type": ["user"], "group_by": "member"},
            ),
            Example(
                "who-is-behind",
                "With groups and project roles expanded into who they are",
                {"scope": SCOPE, "role_risk": "medium", "expand_members": True},
            ),
        ),
    ),
    Capability(
        id="billing-grants",
        title="Risky billing account grants",
        theme="IAM risk",
        route="/v1/billing-grants",
        cli_command="billing-grants",
        docs=("docs/CLI.md#billing-accounts", "docs/API.md#get-v1billing-grants"),
        examples=(
            Example("high", "Who holds a high-risk role on a billing account"),
            Example("by-role", "Rolled up by role", {"group_by": "role"}),
        ),
    ),
    Capability(
        id="types",
        title="Resource type names",
        theme="Reference",
        route="/v1/types",
        cli_command="types",
        docs=("docs/CONCEPTS.md#resource-types", "docs/API.md#get-v1types"),
        examples=(Example("all", "Every friendly type name"),),
        has_gcloud=False,
    ),
)

# Commands and routes deliberately not in the catalogue, with the reason. The
# staleness test accepts these and nothing else.
EXCLUDED_CLI_COMMANDS: Mapping[str, str] = {
    "openapi": "Writes a file; development tooling, not a capability",
    "serve": "Starts the server the catalogue runs on",
    "docs": "Starts the browser that renders this catalogue",
}

BY_ID: Mapping[str, Capability] = {c.id: c for c in CAPABILITIES}


# --- Introspection ------------------------------------------------------------
#
# Imported lazily: `app.cli` and `app.api` are the surfaces this module
# describes, and neither should need the catalogue to import.


def route_for(path: str) -> APIRoute:
    from app.api import app

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path:
            return route
    raise KeyError(f"No route {path}")


def _cli_callback(name: str) -> Callable:
    from app.cli import cli

    for command in cli.registered_commands:
        command_name = command.name or command.callback.__name__.replace("_", "-")
        if command_name == name:
            return command.callback
    raise KeyError(f"No CLI command {name}")


def purpose(capability: Capability) -> str:
    """The one-line purpose, from the route's own summary."""
    return route_for(capability.route).summary or ""


def _help_name(text: str) -> str | None:
    """The `Help` attribute this description is built on, longest match first."""
    shared = [
        (name, value)
        for name, value in vars(Help).items()
        if name.isupper() and isinstance(value, str) and value in text
    ]
    if not shared:
        return None
    return max(shared, key=lambda item: len(item[1]))[0]


@cache
def params(capability_id: str) -> tuple[Param, ...]:
    """A capability's inputs, paired across the surfaces by their shared Help text."""
    capability = BY_ID[capability_id]
    endpoint = route_for(capability.route).endpoint

    cli_by_help: dict[str, tuple[str | None, bool]] = {}
    if capability.cli_command:
        for parameter in inspect.signature(
            _cli_callback(capability.cli_command)
        ).parameters.values():
            info = parameter.default
            if not isinstance(info, (ArgumentInfo, OptionInfo)):
                continue
            name = _help_name(info.help or "")
            if name is None:
                continue
            if isinstance(info, ArgumentInfo):
                cli_by_help[name] = (None, True)
            else:
                flag = next(d for d in info.param_decls if d.startswith("--"))
                cli_by_help[name] = (flag, False)

    found = []
    for parameter in inspect.signature(endpoint).parameters.values():
        description = getattr(parameter.default, "description", None) or ""
        name = _help_name(description)
        flag, positional = cli_by_help.get(name, (None, False)) if name else (None, False)
        annotation = str(parameter.annotation)
        kind = next(
            (k for k in ("list", "bool", "int", "float") if annotation.startswith(k)), "str"
        )
        is_required = getattr(parameter.default, "is_required", lambda: False)()
        found.append(
            Param(
                api=parameter.name,
                cli_flag=flag,
                cli_positional=positional,
                help=getattr(Help, name) if name else description,
                help_name=name,
                is_list=annotation.startswith("list"),
                is_bool=annotation == "bool",
                kind=kind,
                required=is_required,
                suggestions=_suggestions(name),
            )
        )
    return tuple(found)


def _suggestions(help_name: str | None) -> tuple[str, ...]:
    """Values worth offering for a parameter, keyed by the Help text it shares."""
    if help_name == "TYPE":
        return tuple(sorted(core.ASSET_TYPES))
    if help_name == "SORT":
        return tuple(v for f in SORTABLE_FIELDS for v in (f, f"{f} DESC"))
    if help_name == "MEMBER_TYPE":
        return role_risk.MEMBER_TYPES
    if help_name == "RISK":
        return tuple(r.value for r in role_risk.Risk)
    if help_name == "GROUP_BY":
        return ("member", "role")
    return ()


# --- Rendering ----------------------------------------------------------------


def _filled(example: Example, scope: str) -> dict[str, str | bool | list[str]]:
    def fill(value):
        if isinstance(value, str):
            return value.replace(SCOPE, scope)
        if isinstance(value, bool):
            return value
        return [fill(v) for v in value]

    return {key: fill(value) for key, value in example.args.items()}


def _param(capability: Capability, api_name: str) -> Param:
    for param in params(capability.id):
        if param.api == api_name:
            return param
    raise KeyError(f"{capability.id} has no parameter {api_name!r}")


def filled_args(example: Example, scope: str) -> dict:
    """An example's arguments with the scope filled in."""
    return _filled(example, scope)


def to_url(capability: Capability, example: Example, scope: str) -> str:
    """The example as a GET against this tool's API."""
    pairs: list[tuple[str, str]] = []
    for key, value in _filled(example, scope).items():
        if isinstance(value, bool):
            pairs.append((key, "true" if value else "false"))
        elif isinstance(value, list):
            pairs.extend((key, v) for v in value)
        else:
            pairs.append((key, value))
    query = urlencode(pairs)
    return f"{capability.route}?{query}" if query else capability.route


def to_argv(capability: Capability, example: Example, scope: str) -> list[str] | None:
    """The example as `gcpe` arguments, or None where there is no CLI form."""
    if capability.cli_command is None:
        return None
    positional: list[str] = []
    options: list[str] = []
    for key, value in _filled(example, scope).items():
        param = _param(capability, key)
        if param.cli_positional:
            positional.append(value)
        elif param.cli_flag is None:
            raise ValueError(f"{capability.id}.{key} has no CLI equivalent")
        elif isinstance(value, bool):
            if value:
                options.append(param.cli_flag)
        elif isinstance(value, list):
            for v in value:
                options.extend([param.cli_flag, v])
        else:
            options.extend([param.cli_flag, value])
    return [capability.cli_command, *positional, *options]


def to_cli(capability: Capability, example: Example, scope: str) -> str | None:
    """The example as a shell command line."""
    argv = to_argv(capability, example, scope)
    return None if argv is None else shlex.join(["gcpe", *argv])


def to_gcloud(
    capability: Capability, example: Example, scope: str, billing_project: str | None = None
) -> str | None:
    """The example as `gcloud`, compiled exactly as the search would compile it.

    Uses the same pure compilation steps the core does and the same renderers
    as `--show-gcloud`, without making a call. Examples may not use the
    project filter: resolving a project ID to a number is itself a CAI call.
    """
    if not capability.has_gcloud:
        return None
    args = _filled(example, scope)

    if capability.id in ("search", "stream", "summary"):
        if "project" in args:
            raise ValueError("Catalogue examples cannot use `project`: it needs a lookup")
        return gcloud.search_command(
            scope=args["scope"],
            asset_types=core.resolve_asset_types(args.get("type", [])),
            query=build_query(
                free_text=args.get("q", ""),
                labels=args.get("label", []),
                locations=args.get("location", []),
                raw=args.get("raw_query", ""),
            ),
            order_by=core.build_order_by(args.get("sort", [])),
            include_iam=bool(args.get("include_iam")),
            effective_iam=bool(args.get("effective")),
            billing_project=billing_project,
        )
    if capability.id == "grants":
        minimum = role_risk.parse_risk(args.get("role_risk", "high"))
        return gcloud.grants_command(
            args["scope"],
            core.grant_queries(minimum, args.get("member_type", [])),
            billing_project,
            expand=bool(args.get("expand_members")),
        )
    if capability.id == "roles":
        return gcloud.roles_command(args.get("scope"), args.get("risk", "high"))
    if capability.id == "billing-grants":
        return gcloud.billing_grants_command(expand=bool(args.get("expand_members")))
    raise KeyError(f"No gcloud renderer for {capability.id}")


# --- Building an example from user input (the `gcpe docs` builder) -------------
#
# The browser sends form values; these turn them into an Example and say what
# is wrong with them, using the validators the core itself uses, so an error
# here is the error the call would give. Nothing is executed.


def example_from_query(
    capability: Capability, items: Sequence[tuple[str, str]]
) -> tuple[Example, list[str]]:
    """An Example from `(name, value)` pairs, plus any parse errors."""
    known = {p.api: p for p in params(capability.id)}
    args: dict[str, str | bool | list[str]] = {}
    errors: list[str] = []
    for name, raw in items:
        param = known.get(name)
        if param is None:
            errors.append(f"Unknown parameter {name!r} for {capability.route}.")
            continue
        value = raw.strip()
        if param.kind == "list":
            if value:
                args.setdefault(name, []).append(value)
        elif param.kind == "bool":
            if value.lower() in ("true", "1", "on"):
                args[name] = True
        elif value:
            if param.kind in ("int", "float"):
                try:
                    number = float(value) if param.kind == "float" else int(value)
                except ValueError:
                    errors.append(f"{name} must be a number, not {value!r}.")
                    continue
                if number < (0 if param.kind == "float" else 1):
                    errors.append(f"{name} must be at least {0 if param.kind == 'float' else 1}.")
                    continue
            args[name] = value
    for param in known.values():
        if param.required and param.api not in args:
            errors.append(f"{param.api} is required.")
    return Example("custom", "Custom", args), errors


def validate(capability: Capability, example: Example) -> list[str]:
    """What the call would reject, found with the core's own validators."""
    args = example.args
    checks = []
    if "scope" in args:
        if capability.id == "roles":
            checks.append(lambda: core.validate_role_scope(args["scope"]))
        else:
            checks.append(lambda: core.validate_scope(args["scope"]))
    if "type" in args:
        checks.append(lambda: core.resolve_asset_types(list(args["type"])))
    if any(k in args for k in ("q", "label", "location", "raw_query")):
        checks.append(
            lambda: build_query(
                free_text=args.get("q", ""),
                labels=args.get("label", []),
                locations=args.get("location", []),
                raw=args.get("raw_query", ""),
            )
        )
    if "sort" in args:
        checks.append(lambda: core.build_order_by(args["sort"]))
    for key in ("risk", "role_risk"):
        if key in args:
            checks.append(lambda key=key: role_risk.parse_risk(args[key]))
    if "member_type" in args:
        checks.append(lambda: role_risk.validate_member_types(args["member_type"]))
    if args.get("group_by") not in (None, "member", "role"):
        checks.append(
            lambda: _raise(f"Cannot group by {args['group_by']!r}. Choose member or role.")
        )

    errors = []
    for check in checks:
        try:
            check()
        except (core.ResourceExplorerError, QueryError, role_risk.RiskRuleError) as exc:
            errors.append(str(exc))
    return errors


def _raise(message: str) -> None:
    raise core.InvalidFilterError(message)


def render(
    capability: Capability,
    example: Example,
    billing_project: str | None,
    parse_errors: Sequence[str] = (),
) -> dict:
    """The three forms of an example, each with a reason when it cannot be given.

    `parse_errors` come from `example_from_query`; with any of them the
    example is incomplete, so no gcloud form is compiled from it.
    """
    errors = [*parse_errors, *validate(capability, example)]
    notes: dict[str, str] = {}

    try:
        cli = to_cli(capability, example, "")
    except ValueError as exc:
        cli, notes["cli"] = None, str(exc)
    if cli is None and "cli" not in notes:
        notes["cli"] = "No CLI form: use the API, or gcpe search -o json."

    gcloud_form = None
    if not capability.has_gcloud:
        notes["gcloud"] = "No gcloud equivalent: this is gcpe's own data."
    elif errors:
        notes["gcloud"] = "Fix the errors above to see the gcloud form."
    elif "project" in example.args:
        notes["gcloud"] = (
            "The project filter is resolved from IDs to numbers when the search runs, which "
            "takes a call; run the CLI form with --show-gcloud to see the exact command."
        )
    else:
        gcloud_form = to_gcloud(capability, example, "", billing_project)

    return {
        "cli": cli,
        "url": to_url(capability, example, ""),
        "gcloud": gcloud_form,
        "notes": notes,
        "errors": errors,
    }
