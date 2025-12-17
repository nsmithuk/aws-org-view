from typing import Protocol, cast, runtime_checkable

from botocore.client import BaseClient
from mypy_boto3_organizations import OrganizationsClient


@runtime_checkable
class OrganizationsClientProvider(Protocol):
    """A runtime-checkable protocol for retrieving an AWS Organizations client.

    This defines the minimal interface required by higher-level components that need
    to obtain a correctly-configured Organizations client, without being coupled to
    how that client is constructed or refreshed.
    """

    def get_client(self) -> OrganizationsClient:
        """Return a configured AWS Organizations client instance."""
        ...


class DefaultOrganizationsClientProvider:
    """A simple provider that wraps a pre-constructed boto3 Organizations client."""

    def __init__(self, client: BaseClient):
        """Create a provider around an existing boto3 Organizations client."""
        if (
            not isinstance(client, BaseClient)
            and client.meta.service_model.service_name != "organizations"
        ):
            raise TypeError("a boto3 organizations client must be provided")

        self._client = client

    def get_client(self) -> OrganizationsClient:
        """Return the wrapped client, cast to the typed Organizations client interface."""
        return cast(OrganizationsClient, self._client)
