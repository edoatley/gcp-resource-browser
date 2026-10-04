"""The rendered gcloud command must reproduce the request exactly.

Exact strings are pinned, as for query compilation: a command that differs by
one quote runs a different search, and nothing would announce it.
"""

from __future__ import annotations

import shlex

from app.gcloud import search_command


def test_minimal_search() -> None:
    assert search_command("projects/p", [], "") == (
        "gcloud asset search-all-resources \\\n    --scope=projects/p"
    )


def test_types_query_and_order_are_all_passed() -> None:
    command = search_command(
        "organizations/123",
        ["storage.googleapis.com/Bucket", "compute.googleapis.com/Instance"],
        "labels.env:prod location:(europe-west2 OR global)",
        order_by="createTime DESC",
    )

    assert command == (
        "gcloud asset search-all-resources \\\n"
        "    --scope=organizations/123 \\\n"
        "    --asset-types=storage.googleapis.com/Bucket,compute.googleapis.com/Instance \\\n"
        "    --query='labels.env:prod location:(europe-west2 OR global)' \\\n"
        "    --order-by='createTime DESC'"
    )


def test_query_survives_the_shell_unchanged() -> None:
    """Quotes inside a query (namespaced label keys) must reach gcloud intact."""
    query = 'labels."cloud.googleapis.com/location":* NOT name:"it\'s"'

    command = search_command("projects/p", [], query)

    tokens = shlex.split(command.replace("\\\n", " "))
    assert f"--query={query}" in tokens


def test_comma_in_a_type_pattern_uses_the_alternate_delimiter() -> None:
    """A plain comma join would split `{1,3}` into two bogus asset types."""
    command = search_command("projects/p", ["compute.googleapis.com/[a-z]{1,3}"], "")

    assert "--asset-types='^|^compute.googleapis.com/[a-z]{1,3}'" in command


def test_suppression_is_called_out() -> None:
    command = search_command("projects/p", [], "", suppressed=12)

    assert command.startswith("# gcloud has no noise reduction: expect 12 more row(s)")


def test_limit_only_when_truncated() -> None:
    assert "--limit" not in search_command("projects/p", [], "", limit=100)
    assert "--limit=100" in search_command("projects/p", [], "", limit=100, truncated=True)


def test_limit_mismatch_is_warned_when_noise_was_hidden() -> None:
    command = search_command("projects/p", [], "", limit=5, truncated=True, suppressed=3)

    assert "may stop at different rows" in command


def test_iam_adds_the_policy_search() -> None:
    command = search_command("projects/p", ["storage.googleapis.com/Bucket"], "", include_iam=True)

    assert command.endswith(
        "gcloud asset search-all-iam-policies \\\n"
        "    --scope=projects/p \\\n"
        "    --asset-types=storage.googleapis.com/Bucket"
    )


def test_billing_project_is_passed_to_both_commands() -> None:
    """Without it, gcloud bills its own configured project, often without CAI enabled."""
    command = search_command("projects/p", [], "", include_iam=True, billing_project="quota-proj")

    assert command.count("--billing-project=quota-proj") == 2
