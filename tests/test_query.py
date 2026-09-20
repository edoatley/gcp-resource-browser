"""Tests for CAI query construction.

The highest-risk code in the project: a filter that compiles to the wrong query
returns a plausible-looking result set that is quietly wrong, which no amount of
faking the client can detect. These tests pin the exact string sent upstream.
"""

from __future__ import annotations

import pytest

from app.query import QueryError, build_query, label_term, quote


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("prod", "prod"),
        ("europe-west2", "europe-west2"),
        # `*` stays bare, so CAI wildcards keep working.
        ("europe-*", "europe-*"),
        ("my_bucket.v2/x", "my_bucket.v2/x"),
    ],
)
def test_safe_values_are_emitted_bare(value: str, expected: str) -> None:
    assert quote(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("my value", '"my value"'),
        ("a OR b", '"a OR b"'),
        ("paren)", '"paren)"'),
        ('has"quote', '"has\\"quote"'),
        ("back\\slash", '"back\\\\slash"'),
    ],
)
def test_structural_characters_are_quoted(value: str, expected: str) -> None:
    """A value must never be able to change the shape of the query."""
    assert quote(value) == expected


def test_rejects_control_characters() -> None:
    with pytest.raises(QueryError):
        quote("bad\nvalue")


def test_rejects_empty_value() -> None:
    with pytest.raises(QueryError):
        quote("")


def test_label_key_value() -> None:
    assert label_term("env=prod") == "labels.env:prod"


def test_bare_label_key_matches_any_value() -> None:
    assert label_term("env") == "labels.env:*"


def test_label_value_is_quoted_when_unsafe() -> None:
    assert label_term("owner=platform team") == 'labels.owner:"platform team"'


@pytest.mark.parametrize("spec", ["Env=prod", "9lives=x", "-bad=x", "=prod", ""])
def test_rejects_invalid_label_keys(spec: str) -> None:
    with pytest.raises(QueryError):
        label_term(spec)


def test_rejects_label_with_empty_value() -> None:
    """`env=` is almost certainly a mistake, not a request for any value."""
    with pytest.raises(QueryError):
        label_term("env=")


def test_single_location_is_a_plain_term() -> None:
    assert build_query(locations=["europe-west2"]) == "location:europe-west2"


def test_several_locations_are_ored() -> None:
    assert build_query(locations=["europe-west2", "global"]) == (
        "location:(europe-west2 OR global)"
    )


def test_different_filter_kinds_are_anded() -> None:
    assert build_query(free_text="backup", labels=["env=prod"], locations=["us-central1"]) == (
        "backup labels.env:prod location:us-central1"
    )


def test_several_labels_are_anded() -> None:
    """Two label filters mean both must hold, not either."""
    assert build_query(labels=["env=prod", "tier=web"]) == ("labels.env:prod labels.tier:web")


def test_empty_filters_produce_an_empty_query() -> None:
    assert build_query() == ""


def test_raw_query_is_appended_verbatim() -> None:
    assert build_query(labels=["env=prod"], raw="NOT state:ACTIVE") == (
        "labels.env:prod NOT state:ACTIVE"
    )


def test_injection_attempt_is_neutralised() -> None:
    """A crafted value must be matched literally, not alter the query."""
    query = build_query(labels=["env=prod) OR (labels.env:dev"])
    assert query == 'labels.env:"prod) OR (labels.env:dev"'


# --- namespaced label keys --------------------------------------------------
# Google's own system labels are namespaced and are everywhere in a real
# estate: cloud.googleapis.com/location, serving.knative.dev/service,
# run.googleapis.com/startupProbeType. Verified against the live API that CAI
# rejects them unquoted with "400 Unsupported field", so quoting is mandatory
# rather than cosmetic.


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (
            "cloud.googleapis.com/location=us-central1",
            'labels."cloud.googleapis.com/location":us-central1',
        ),
        (
            "serving.knative.dev/service=sudoku-rcg",
            'labels."serving.knative.dev/service":sudoku-rcg',
        ),
        (
            "run.googleapis.com/startupProbeType=Default",
            'labels."run.googleapis.com/startupProbeType":Default',
        ),
    ],
)
def test_namespaced_label_keys_are_quoted(spec: str, expected: str) -> None:
    assert label_term(spec) == expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("env=prod", "labels.env:prod"),
        ("managed_by=terraform", "labels.managed_by:terraform"),
        ("goog-terraform-provisioned=true", "labels.goog-terraform-provisioned:true"),
    ],
)
def test_plain_label_keys_stay_bare(spec: str, expected: str) -> None:
    """Quoting a plain key is harmless but makes the query needlessly noisy."""
    assert label_term(spec) == expected


def test_namespaced_key_with_no_value_matches_any() -> None:
    assert label_term("cloud.googleapis.com/location") == 'labels."cloud.googleapis.com/location":*'


@pytest.mark.parametrize("spec", ['bad"key=x', "has space=x", "(paren)=x", "UPPER.com/x=y"])
def test_still_rejects_keys_that_could_break_the_query(spec: str) -> None:
    with pytest.raises(QueryError):
        label_term(spec)
