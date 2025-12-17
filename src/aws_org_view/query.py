import boto3
from botocore.client import BaseClient
from cachetools import TTLCache
from mypy_boto3_organizations import OrganizationsClient

from .client import DefaultOrganizationsClientProvider, OrganizationsClientProvider


class ParentResolutionError(RuntimeError):
    pass


class OUMembershipRetrieverResult(dict):
    def get_accounts(self) -> list:
        """
        Returns all accounts within the OU hierarchy.
        :return:
        """
        if len(self) != 1:
            raise ValueError("Expected exactly one root in OU membership result")
        child = next(iter(self.values()))
        return self._get_accounts(child)

    def _get_accounts(self, unit: dict) -> list:
        ou_children = unit.get("org_units", {})
        accounts = list(unit.get("accounts", []))

        for child in ou_children.values():
            accounts.extend(self._get_accounts(child))

        return accounts


class AwsOrgView:
    def __init__(
        self,
        client: BaseClient | OrganizationsClientProvider | None = None,
        cache_ttl: int = 3600,
        cache_maxsize: int = 512,
    ):
        if client is None:
            # We default to the default client
            self._client_provider: OrganizationsClientProvider = (
                DefaultOrganizationsClientProvider(boto3.client("organizations"))
            )
        elif isinstance(client, BaseClient):
            # We use the passed client
            if client.meta.service_model.service_name != "organizations":
                raise TypeError("BaseClient must be an Organizations client")

            self._client_provider: OrganizationsClientProvider = (
                DefaultOrganizationsClientProvider(client)
            )
        elif isinstance(client, OrganizationsClientProvider):
            self._client_provider: OrganizationsClientProvider = client
        else:
            raise TypeError(
                "client must be a boto3 Organizations client, an OrganizationsClientProvider, or None"
            )

        self._get_parent_cache = TTLCache(maxsize=cache_maxsize, ttl=cache_ttl)
        self._get_org_root_cache = TTLCache(maxsize=1, ttl=cache_ttl)
        self._describe_organizational_unit_cache = TTLCache(
            maxsize=cache_maxsize, ttl=cache_ttl
        )
        self._list_accounts_cache = TTLCache(maxsize=cache_maxsize, ttl=cache_ttl)
        self._list_child_ous_cache = TTLCache(maxsize=cache_maxsize, ttl=cache_ttl)

    def _get_client(self) -> OrganizationsClient:
        # TODO: clear cache is session changes?
        return self._client_provider.get_client()

    # --------------------------------------------------------------------------------
    # Account in haystack lookup

    def account_in_haystack(
        self,
        account_id: str,
        haystack: set[str] | list[str],
        require_direct_descendant: bool = False,
    ) -> bool:
        """Check if an account belongs to any of the specified OUs or their descendants.

        This method traverses up the OU hierarchy from the account to the root,
        checking at each level if the current OU matches any of the target OUs.

        Args:
            account_id: The AWS account ID to check
            haystack: Set or list of account IDs or OU IDs to check against. Can include
                          account IDs, OU IDs (ou-*) and root IDs (r-*).
            require_direct_descendant: If True, the account_id must be a direct descendant of a matched OU.

        Returns:
            bool: True if the account is in any of the target OUs or their
                 descendants, False otherwise

        Note:
            - The method will traverse up to 6 levels (5 OUs + root) as per AWS
              Organizations limits
            - The search stops when:
                1. A matching OU is found
                2. The root level is reached
                3. No parent is found
                4. Maximum depth is reached
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

            current_id = self._get_parent(current_id)
            if not current_id:
                break

        return False

    def _get_parent(self, child_id: str) -> str | None:
        """Get the parent OU or root ID for a given child ID.

        Uses a TTL cache to reduce API calls to AWS Organizations.

        Args:
            child_id: ID of the child account or OU to find parent for

        Returns:
            str | None: ID of the parent OU or root, None if not found

        Raises:
            ValueError: If the child has no parent or multiple parents
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
            parent_id: self._build_ou_hierarchy(
                parent_id, parent_name, direct_descendants_only
            )
        }

        return OUMembershipRetrieverResult(result)

    def _get_org_root(self) -> str:
        if "id" in self._get_org_root_cache:
            return self._get_org_root_cache["id"]

        roots = self._get_client().list_roots()["Roots"]
        if len(roots) != 1:
            raise RuntimeError("Expected exactly one organization root")

        ident = roots[0]["Id"]
        self._get_org_root_cache["id"] = ident

        return ident

    def _describe_organizational_unit(self, ou_id: str):
        if ou_id in self._describe_organizational_unit_cache:
            return self._describe_organizational_unit_cache[ou_id]

        details = self._get_client().describe_organizational_unit(
            OrganizationalUnitId=ou_id
        )

        self._describe_organizational_unit_cache[ou_id] = details
        return details

    def _build_ou_hierarchy(
        self, parent_id: str, parent_name: str, direct_descendants_only: bool
    ) -> dict:
        """Recursively build the OU structure."""
        ou_tree = {"name": parent_name, "accounts": self._list_accounts(parent_id)}

        if direct_descendants_only:
            return ou_tree

        ou_tree["org_units"] = {}

        child_ous = self._list_child_ous(parent_id)
        for ou in child_ous:
            ou_id = ou["Id"]
            ou_tree["org_units"][ou_id] = self._build_ou_hierarchy(
                ou_id, ou["Name"], False
            )

        return ou_tree

    def _list_accounts(self, parent_id: str) -> list[dict]:
        """List accounts directly under the given parent ID (OU or Root)."""
        if parent_id in self._list_accounts_cache:
            return self._list_accounts_cache[parent_id]

        accounts = []
        paginator = self._get_client().get_paginator("list_accounts_for_parent")
        for page in paginator.paginate(ParentId=parent_id):
            accounts.extend(page["Accounts"])

        self._list_accounts_cache[parent_id] = accounts
        return accounts

    def _list_child_ous(self, parent_id: str) -> list[dict]:
        """List immediate child OUs of the given parent."""
        if parent_id in self._list_child_ous_cache:
            return self._list_child_ous_cache[parent_id]

        ous = []
        paginator = self._get_client().get_paginator(
            "list_organizational_units_for_parent"
        )
        for page in paginator.paginate(ParentId=parent_id):
            ous.extend(page["OrganizationalUnits"])

        self._list_child_ous_cache[parent_id] = ous
        return ous
