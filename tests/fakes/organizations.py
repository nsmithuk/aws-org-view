from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class FakePaginator:
    pages: list[dict[str, Any]]
    paginate_calls: list[dict[str, Any]] = field(default_factory=list)

    def paginate(self, **kwargs: Any) -> Iterable[dict[str, Any]]:
        self.paginate_calls.append(kwargs)
        return list(self.pages)


@dataclass
class FakeOrganizationsClient:
    roots: list[dict[str, Any]] = field(default_factory=lambda: [{"Id": "r-root"}])
    parents_by_child: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {
            "111111111111": [{"Id": "ou-a"}],
            "ou-a": [{"Id": "r-root"}],
        }
    )
    ou_details_by_id: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {"ou-a": {"OrganizationalUnit": {"Id": "ou-a", "Name": "OU-A"}}}
    )
    accounts_pages_by_parent: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    ous_pages_by_parent: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    list_roots_calls: int = 0
    list_parents_calls: list[dict[str, Any]] = field(default_factory=list)
    describe_ou_calls: list[dict[str, Any]] = field(default_factory=list)
    get_paginator_calls: list[str] = field(default_factory=list)

    def list_roots(self) -> dict[str, Any]:
        self.list_roots_calls += 1
        return {"Roots": list(self.roots)}

    def list_parents(self, *, ChildId: str) -> dict[str, Any]:
        self.list_parents_calls.append({"ChildId": ChildId})
        parents = self.parents_by_child.get(ChildId, [{"Id": "r-root"}])
        return {"Parents": list(parents)}

    def describe_organizational_unit(self, *, OrganizationalUnitId: str) -> dict[str, Any]:
        self.describe_ou_calls.append({"OrganizationalUnitId": OrganizationalUnitId})
        return self.ou_details_by_id.get(
            OrganizationalUnitId,
            {"OrganizationalUnit": {"Id": OrganizationalUnitId}},
        )

    def get_paginator(self, name: str):
        self.get_paginator_calls.append(name)

        if name == "list_accounts_for_parent":
            return _PerParentPaginator(self, kind="accounts")

        if name == "list_organizational_units_for_parent":
            return _PerParentPaginator(self, kind="ous")

        raise AssertionError(f"Unexpected paginator requested: {name}")


class _PerParentPaginator(FakePaginator):
    def __init__(self, client: FakeOrganizationsClient, kind: str) -> None:
        super().__init__(pages=[])
        self._client = client
        self._kind = kind

    def paginate(self, **kwargs: Any) -> Iterable[dict[str, Any]]:
        self.paginate_calls.append(kwargs)
        parent_id = kwargs["ParentId"]
        if self._kind == "accounts":
            return list(self._client.accounts_pages_by_parent.get(parent_id, [{"Accounts": []}]))
        return list(self._client.ous_pages_by_parent.get(parent_id, [{"OrganizationalUnits": []}]))


class FakeOrganizationsClientProvider:
    def __init__(self, client: FakeOrganizationsClient) -> None:
        self._client = client
        self.get_client_calls = 0

    def get_client(self):
        self.get_client_calls += 1
        return self._client
