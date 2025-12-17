import pytest

from aws_org_view import OUMembershipRetrieverResult


# --------------------------------------------------------------------------------------
# account_in_haystack tests

@pytest.mark.parametrize(
    "haystack",
    [
        ["ou-a"],            # list input
        {"ou-a"},            # set input
        ["ou-a", "ou-x"],    # list with extras
    ],
)
def test_account_in_haystack_accepts_list_or_set(view, haystack):
    assert view.account_in_haystack("111111111111", haystack) is True


def test_account_in_haystack_returns_true_when_root_is_in_haystack(view):
    # account -> ou-a -> r-root ; should succeed when it reaches the root
    assert view.account_in_haystack("111111111111", {"r-root"}) is True


def test_account_in_haystack_stops_after_reaching_root_and_does_not_loop(view, org_client):
    # Ensure that once the chain reaches r-root, no further list_parents calls happen.
    org_client.list_parents_calls.clear()

    assert view.account_in_haystack("111111111111", {"nope"}) is False

    # With the default mapping, it should resolve:
    # 111... -> ou-a
    # ou-a   -> r-root
    # and then stop (because current_id startswith "r-")
    assert org_client.list_parents_calls == [
        {"ChildId": "111111111111"},
        {"ChildId": "ou-a"},
    ]


def test_account_in_haystack_breaks_when_parent_resolution_returns_none(view, org_client):
    # Simulate a broken chain where list_parents returns a single parent without Id -> parent_id=None.
    org_client.parents_by_child["111111111111"] = [{}]

    # Should not error; should just break and return False (unless account itself in haystack)
    assert view.account_in_haystack("111111111111", {"ou-a"}) is False


def test_account_in_haystack_does_not_call_list_parents_if_self_in_haystack(view, org_client):
    # Reset call history
    org_client.list_parents_calls.clear()

    assert view.account_in_haystack("111111111111", {"111111111111"}) is True
    assert org_client.list_parents_calls == []


def test_account_in_haystack_calls_list_parents_only_as_needed(view, org_client):
    org_client.list_parents_calls.clear()

    # Needs exactly one hop to find ou-a
    assert view.account_in_haystack("111111111111", {"ou-a"}) is True
    assert org_client.list_parents_calls == [{"ChildId": "111111111111"}]


def test_account_in_haystack_direct_descendant_only_makes_at_most_one_parent_lookup(view, org_client):
    org_client.list_parents_calls.clear()

    assert view.account_in_haystack(
        "111111111111",
        {"r-root"},
        require_direct_descendant=True,
    ) is False

    # It will resolve the parent once (account -> parent) then stop.
    assert org_client.list_parents_calls == [{"ChildId": "111111111111"}]


def test_account_in_haystack_uses_cache_on_repeated_calls(view, org_client):
    org_client.list_parents_calls.clear()

    assert view.account_in_haystack("111111111111", {"ou-a"}) is True
    assert view.account_in_haystack("111111111111", {"ou-a"}) is True

    # Parent lookup should have been cached, so list_parents only called once.
    assert org_client.list_parents_calls == [{"ChildId": "111111111111"}]


# --------------------------------------------------------------------------------------
# get_ou_hierarchy tests

def test_get_ou_hierarchy_returns_ou_membership_result(view, org_client):
    org_client.accounts_pages_by_parent["r-root"] = [{"Accounts": []}]
    org_client.ous_pages_by_parent["r-root"] = [{"OrganizationalUnits": []}]

    result = view.get_ou_hierarchy()
    assert isinstance(result, OUMembershipRetrieverResult)


def test_get_ou_hierarchy_root_id_sets_root_name(view, org_client):
    # When parent_id starts with "r-", it should label it "Organization Root" (no describe call).
    org_client.accounts_pages_by_parent["r-root"] = [{"Accounts": []}]
    org_client.ous_pages_by_parent["r-root"] = [{"OrganizationalUnits": []}]
    org_client.describe_ou_calls.clear()

    result = view.get_ou_hierarchy(parent_id="r-root")
    assert result["r-root"]["name"] == "Organization Root"
    assert org_client.describe_ou_calls == []  # never described a root


def test_get_ou_hierarchy_for_ou_id_calls_describe_once_due_to_cache(view, org_client):
    org_client.accounts_pages_by_parent["ou-a"] = [{"Accounts": []}]
    org_client.ous_pages_by_parent["ou-a"] = [{"OrganizationalUnits": []}]
    org_client.describe_ou_calls.clear()

    view.get_ou_hierarchy(parent_id="ou-a")
    view.get_ou_hierarchy(parent_id="ou-a")

    # _describe_organizational_unit is cached inside AwsOrgView
    assert org_client.describe_ou_calls == [{"OrganizationalUnitId": "ou-a"}]


def test_get_ou_hierarchy_builds_recursive_tree_with_accounts_and_child_ous(view, org_client):
    """
    Build:
      r-root
        accounts: [A1]
        ous: ou-a, ou-b
      ou-a
        accounts: [A2]
        ous: ou-c
      ou-b
        accounts: []
        ous: []
      ou-c
        accounts: [A3]
        ous: []
    """
    # Root
    org_client.accounts_pages_by_parent["r-root"] = [{"Accounts": [{"Id": "A1"}]}]
    org_client.ous_pages_by_parent["r-root"] = [
        {"OrganizationalUnits": [{"Id": "ou-a", "Name": "OU-A"}, {"Id": "ou-b", "Name": "OU-B"}]}
    ]

    # Children
    org_client.accounts_pages_by_parent["ou-a"] = [{"Accounts": [{"Id": "A2"}]}]
    org_client.ous_pages_by_parent["ou-a"] = [{"OrganizationalUnits": [{"Id": "ou-c", "Name": "OU-C"}]}]

    org_client.accounts_pages_by_parent["ou-b"] = [{"Accounts": []}]
    org_client.ous_pages_by_parent["ou-b"] = [{"OrganizationalUnits": []}]

    org_client.accounts_pages_by_parent["ou-c"] = [{"Accounts": [{"Id": "A3"}]}]
    org_client.ous_pages_by_parent["ou-c"] = [{"OrganizationalUnits": []}]

    result = view.get_ou_hierarchy(parent_id="r-root", direct_descendants_only=False)

    root = result["r-root"]
    assert root["name"] == "Organization Root"
    assert [a["Id"] for a in root["accounts"]] == ["A1"]

    assert set(root["org_units"].keys()) == {"ou-a", "ou-b"}

    ou_a = root["org_units"]["ou-a"]
    assert ou_a["name"] == "OU-A"
    assert [a["Id"] for a in ou_a["accounts"]] == ["A2"]
    assert set(ou_a["org_units"].keys()) == {"ou-c"}

    ou_c = ou_a["org_units"]["ou-c"]
    assert ou_c["name"] == "OU-C"
    assert [a["Id"] for a in ou_c["accounts"]] == ["A3"]
    assert ou_c["org_units"] == {}

    ou_b = root["org_units"]["ou-b"]
    assert ou_b["name"] == "OU-B"
    assert ou_b["accounts"] == []
    assert ou_b["org_units"] == {}


def test_get_ou_hierarchy_direct_descendants_only_includes_accounts_and_shallow_child_ous(view, org_client):
    org_client.accounts_pages_by_parent["r-root"] = [{"Accounts": [{"Id": "A1"}]}]
    org_client.ous_pages_by_parent["r-root"] = [
        {"OrganizationalUnits": [{"Id": "ou-a", "Name": "OU-A"}]}
    ]

    # Even if ou-a has data, direct_descendants_only=True should prevent recursing into it.
    org_client.accounts_pages_by_parent["ou-a"] = [{"Accounts": [{"Id": "A2"}]}]
    org_client.ous_pages_by_parent["ou-a"] = [{"OrganizationalUnits": [{"Id": "ou-c", "Name": "OU-C"}]}]

    result = view.get_ou_hierarchy(parent_id="r-root", direct_descendants_only=True)

    root = result["r-root"]
    assert [a["Id"] for a in root["accounts"]] == ["A1"]

    # org_units is always present now
    assert root["org_units"] == {"ou-a": {"name": "OU-A"}}

    # Explicitly assert it did NOT recurse into ou-a
    assert "accounts" not in root["org_units"]["ou-a"]
    assert "org_units" not in root["org_units"]["ou-a"]



def test_get_ou_hierarchy_get_accounts_flattening_matches_tree(view, org_client):
    # Small 2-level tree
    org_client.accounts_pages_by_parent["r-root"] = [{"Accounts": [{"Id": "A1"}]}]
    org_client.ous_pages_by_parent["r-root"] = [{"OrganizationalUnits": [{"Id": "ou-a", "Name": "OU-A"}]}]

    org_client.accounts_pages_by_parent["ou-a"] = [{"Accounts": [{"Id": "A2"}]}]
    org_client.ous_pages_by_parent["ou-a"] = [{"OrganizationalUnits": []}]

    result = view.get_ou_hierarchy(parent_id="r-root", direct_descendants_only=False)
    accounts = result.get_accounts()
    assert [a["Id"] for a in accounts] == ["A1", "A2"]


def test_get_ou_hierarchy_uses_account_and_ou_caches(view, org_client):
    """
    Calling get_ou_hierarchy twice should reuse the list_accounts/list_child_ous caches,
    so the fake client should only have requested each paginator once per parent.
    """
    org_client.accounts_pages_by_parent["r-root"] = [{"Accounts": []}]
    org_client.ous_pages_by_parent["r-root"] = [{"OrganizationalUnits": [{"Id": "ou-a", "Name": "OU-A"}]}]

    org_client.accounts_pages_by_parent["ou-a"] = [{"Accounts": []}]
    org_client.ous_pages_by_parent["ou-a"] = [{"OrganizationalUnits": []}]

    org_client.get_paginator_calls.clear()

    view.get_ou_hierarchy(parent_id="r-root", direct_descendants_only=False)
    view.get_ou_hierarchy(parent_id="r-root", direct_descendants_only=False)

    # Each paginator should have been requested once per method type because results are cached.
    assert org_client.get_paginator_calls.count("list_accounts_for_parent") == 2
    assert org_client.get_paginator_calls.count("list_organizational_units_for_parent") == 2
    # Explanation: root and ou-a each need accounts + child OUs on the first run.
    # Second run should hit caches and not call get_paginator again.
