"""Aggregation across CAI surfaces, and the summary rollup."""

from __future__ import annotations

from app.aggregate import ATTACHED_IAM_NOTE, attach_iam, summarise
from app.models import IamBinding, Resource


def resource(name: str, asset_type: str = "storage.googleapis.com/Bucket", **kw) -> Resource:
    return Resource(full_name=name, asset_type=asset_type, **kw)


def test_bindings_join_on_full_resource_name() -> None:
    """IamPolicySearchResult.resource is the same string CAI reports as `name`."""
    resources = [resource("//storage.googleapis.com/b1")]
    bindings = {
        "//storage.googleapis.com/b1": [IamBinding(role="roles/storage.admin", members=["user:a"])]
    }

    joined = attach_iam(resources, bindings)

    assert joined[0].iam_bindings[0].role == "roles/storage.admin"


def test_resource_without_a_policy_gets_an_empty_list_not_none() -> None:
    """None means IAM was never asked for; [] means asked, none attached.

    Conflating them would let a reader mistake "not requested" for "nothing
    granted", which is the dangerous direction for an access audit.
    """
    joined = attach_iam([resource("//x/none")], {})

    assert joined[0].iam_bindings == []
    assert joined[0].iam_bindings is not None


def test_unrequested_iam_stays_none() -> None:
    assert resource("//x/a").iam_bindings is None


def test_the_attached_only_caveat_is_stated() -> None:
    """Attached-only bindings are easy to mistake for effective access."""
    assert "Inherited" in ATTACHED_IAM_NOTE
    assert "NOT included" in ATTACHED_IAM_NOTE


def test_summary_counts_by_every_dimension() -> None:
    resources = [
        resource("//x/1", project_id="p1", location="europe-west2"),
        resource("//x/2", project_id="p1", location="europe-west2"),
        resource("//x/3", asset_type="compute.googleapis.com/Instance", project_id="p2",
                 location="us-central1"),
    ]

    totals = summarise("organizations/1", resources, suppressed=7)

    assert totals.total == 3
    assert totals.suppressed == 7
    assert totals.by_asset_type["storage.googleapis.com/Bucket"] == 2
    assert totals.by_project == {"p1": 2, "p2": 1}
    assert totals.by_location == {"europe-west2": 2, "us-central1": 1}


def test_counts_are_ordered_by_size_then_name() -> None:
    """Equal counts must order predictably, not by dict insertion."""
    resources = [
        resource("//x/1", project_id="zebra"),
        resource("//x/2", project_id="alpha"),
        resource("//x/3", project_id="middle"),
        resource("//x/4", project_id="middle"),
    ]

    totals = summarise("organizations/1", resources, suppressed=0)

    assert list(totals.by_project) == ["middle", "alpha", "zebra"]


def test_summary_prefers_the_project_id_over_the_number() -> None:
    resources = [resource("//x/1", project="734077548565", project_id="idp-prototype-edo")]

    assert summarise("projects/p", resources, 0).by_project == {"idp-prototype-edo": 1}


def test_missing_dimensions_are_labelled_not_dropped() -> None:
    """A resource with no location still needs counting, or the total misleads."""
    totals = summarise("projects/p", [resource("//x/1")], 0)

    assert totals.by_location == {"<none>": 1}
    assert totals.by_project == {"<unknown>": 1}
    assert sum(totals.by_location.values()) == totals.total
