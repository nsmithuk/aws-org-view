import pytest

from aws_org_view import OUMembershipRetrieverResult, ParentResolutionError


def test_get_parent_calls_list_parents_once_due_to_cache(view, org_client):
    assert view._get_parent("111111111111") == "ou-a"
    assert view._get_parent("111111111111") == "ou-a"
    assert len(org_client.list_parents_calls) == 1


def test_get_parent_raises_when_multiple_parents(view, org_client):
    org_client.parents_by_child["111111111111"] = [{"Id": "ou-a"}, {"Id": "ou-b"}]
    with pytest.raises(ParentResolutionError):
        view._get_parent("111111111111")
