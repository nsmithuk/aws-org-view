from typing import Any

import boto3
from botocore.client import BaseClient
from cachetools import TTLCache
from mypy_boto3_organizations import OrganizationsClient
from mypy_boto3_organizations.type_defs import (
    AccountTypeDef,
    OrganizationalUnitTypeDef,
)

from .client import DefaultOrganizationsClientProvider, OrganizationsClientProvider


class ParentResolutionError(RuntimeError):
    """Raised when an AWS Organizations entity cannot be resolved to exactly one parent."""


class OUMembershipRetrieverResult(dict):
    """A convenience wrapper around a nested OU/account hierarchy tree."""

    def get_accounts(self) -> list:
        """Return a flat list of all accounts contained anywhere within the hierarchy.

        This expects the result to contain exactly one root node (the requested parent),
        then recursively walks all nested organizational units collecting accounts.
        """
        if len(self) != 1:
            raise ValueError("Expected exactly one root in OU membership result")
        child = next(iter(self.values()))
        return self._get_accounts(child)

    def _get_accounts(self, unit: dict) -> list:
        """Recursively traverse a hierarchy node and collect all accounts beneath it."""
        ou_children = unit.get("org_units", {})
        accounts = list(unit.get("accounts", []))

        for child in ou_children.values():
            accounts.extend(self._get_accounts(child))

        return accounts


class AwsOrgView:
    """High-level view over AWS Organizations that adds caching and hierarchy helpers."""

    def __init__(
        self,
        client: BaseClient | OrganizationsClientProvider | None = None,
        cache_ttl: int = 3600,
        cache_maxsize: int = 512,
    ):
        """Create an Organizations view backed by either a boto3 client or a client provider.

        The view stores short-lived results in TTL caches to reduce repeated API calls when
        walking OU/account relationships and building hierarchy trees.
        """
        self._client_provider: OrganizationsClientProvider

        if client is None:
            # We default to the default client
            self._client_provider = DefaultOrganizationsClientProvider(
                boto3.client("organizations")
            )
        elif isinstance(client, BaseClient):
            # We use the passed client
            if client.meta.service_model.service_name != "organizations":
                raise TypeError("BaseClient must be an Organizations client")

            self._client_provider = DefaultOrganizationsClientProvider(client)
        elif isinstance(client, OrganizationsClientProvider):
            self._client_provider = client
        else:
            raise TypeError(
                "client must be a boto3 Organizations client, an OrganizationsClientProvider, or None"
            )

        self._get_parent_cache: TTLCache[str, Any] = TTLCache(maxsize=cache_maxsize, ttl=cache_ttl)
        self._get_org_root_cache: TTLCache[str, Any] = TTLCache(maxsize=1, ttl=cache_ttl)
        self._describe_organizational_unit_cache: TTLCache[str, Any] = TTLCache(
            maxsize=cache_maxsize, ttl=cache_ttl
        )
        self._list_accounts_cache: TTLCache[str, Any] = TTLCache(
            maxsize=cache_maxsize, ttl=cache_ttl
        )
        self._list_child_ous_cache: TTLCache[str, Any] = TTLCache(
            maxsize=cache_maxsize, ttl=cache_ttl
        )

    def _get_client(self) -> OrganizationsClient:
        """Return an Organizations client from the configured provider."""
        return self._client_provider.get_client()

    # --------------------------------------------------------------------------------
    # Account in haystack lookup

    def account_in_haystack(
        self,
        account_id: str,
        haystack: set[str] | list[str],
        require_direct_descendant: bool = False,
    ) -> bool:
        """Determine whether an account is within a target set of OUs/roots/accounts.

        This walks upward from an account through its parent chain, checking each
        encountered identifier against the provided haystack. The search can optionally
        be constrained to only direct descendants.
        """
        current_id = account_id

        if not isinstance(haystack, set):
            haystack = set(haystack)

        # AWS supports a max depth of 5 OUs, plus the root.
        for i in range(6):
            if current_id in haystack:
                return True

            # We break early if we're looking only for direct descendents
            if require_direct_descendant and i == 1:
                break

            # We've hit the root.
            if current_id.startswith("r-"):
                break

            parent_id = self._get_parent(current_id)
            if not parent_id:
                break

            current_id = parent_id

        return False

    def _get_parent(self, child_id: str) -> str | None:
        """Resolve the single parent (OU/root) identifier for the given child ID.

        Results are cached to avoid repeated AWS Organizations `list_parents` calls
        during hierarchy traversal.
        """
        if child_id in self._get_parent_cache:
            return self._get_parent_cache[child_id]

        response = self._get_client().list_parents(ChildId=child_id)
        parents = response["Parents"]

        if len(parents) != 1:
            raise ParentResolutionError(
                f"Expected exactly one parent for {child_id}, got {len(parents)}"
            )

        parent_id = parents[0].get("Id", None)
        self._get_parent_cache[child_id] = parent_id
        return parent_id

    # --------------------------------------------------------------------------------
    #

    def get_ou_hierarchy(
        self, parent_id: str | None = None, direct_descendants_only: bool = False
    ) -> OUMembershipRetrieverResult:
        """Build and return a nested representation of OUs and accounts under a parent.

        If no parent is provided, the organization root is used. The returned structure
        can optionally include only immediate children, or recurse through descendants.
        """
        if parent_id is None:
            parent_id = self._get_org_root()
            parent_name = "Organization Root"
        elif parent_id.startswith("r-"):
            parent_name = "Organization Root"
        else:
            details = self._describe_organizational_unit(parent_id)
            parent_name = details.get("OrganizationalUnit", {}).get("Name")
            if not parent_name:
                raise ValueError(f"Unable to determine name for OU {parent_id}")

        result = {
            parent_id: self._build_ou_hierarchy(parent_id, parent_name, direct_descendants_only)
        }

        return OUMembershipRetrieverResult(result)

    def _get_org_root(self) -> str:
        """Return the single organization root ID, using a cached value when available."""
        if "id" in self._get_org_root_cache:
            return self._get_org_root_cache["id"]

        roots = self._get_client().list_roots()["Roots"]
        if len(roots) != 1:
            raise RuntimeError("Expected exactly one organization root")

        ident = roots[0]["Id"]
        self._get_org_root_cache["id"] = ident

        return ident

    def _describe_organizational_unit(self, ou_id: str):
        """Describe a single organizational unit, caching results to reduce API calls."""
        if ou_id in self._describe_organizational_unit_cache:
            return self._describe_organizational_unit_cache[ou_id]

        details = self._get_client().describe_organizational_unit(OrganizationalUnitId=ou_id)

        self._describe_organizational_unit_cache[ou_id] = details
        return details

    def _build_ou_hierarchy(
        self, parent_id: str, parent_name: str, direct_descendants_only: bool
    ) -> dict:
        """Recursively construct a nested OU tree with accounts attached at each node."""
        ou_tree: dict[str, Any] = {
            "name": parent_name,
            "accounts": self._list_accounts(parent_id),
            "org_units": {},
        }

        child_ous = self._list_child_ous(parent_id)
        for ou in child_ous:
            ou_id = ou["Id"]
            if direct_descendants_only:
                ou_tree["org_units"][ou_id] = {"name": ou["Name"]}
            else:
                ou_tree["org_units"][ou_id] = self._build_ou_hierarchy(ou_id, ou["Name"], False)

        return ou_tree

    def _list_accounts(self, parent_id: str) -> list[AccountTypeDef]:
        """List all accounts directly under the given parent (OU or root), with caching."""
        if parent_id in self._list_accounts_cache:
            return self._list_accounts_cache[parent_id]

        accounts = []
        paginator = self._get_client().get_paginator("list_accounts_for_parent")
        for page in paginator.paginate(ParentId=parent_id):
            accounts.extend(page["Accounts"])

        self._list_accounts_cache[parent_id] = accounts
        return accounts

    def _list_child_ous(self, parent_id: str) -> list[OrganizationalUnitTypeDef]:
        """List all immediate child OUs for the given parent (OU or root), with caching."""
        if parent_id in self._list_child_ous_cache:
            return self._list_child_ous_cache[parent_id]

        ous = []
        paginator = self._get_client().get_paginator("list_organizational_units_for_parent")
        for page in paginator.paginate(ParentId=parent_id):
            ous.extend(page["OrganizationalUnits"])

        self._list_child_ous_cache[parent_id] = ous
        return ous
