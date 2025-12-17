from typing import Protocol, cast, runtime_checkable

from botocore.client import BaseClient
from mypy_boto3_organizations import OrganizationsClient


@runtime_checkable
class OrganizationsClientProvider(Protocol):
    """Protocol defining the interface for AWS Organizations client providers."""

    def get_client(self) -> OrganizationsClient:
        """Retrieve an AWS Organizations client.

        Returns:
            OrganizationsClient: A configured AWS Organizations client
        """
        ...


class DefaultOrganizationsClientProvider:
    def __init__(self, client: BaseClient):
        if (
            not isinstance(client, BaseClient)
            and client.meta.service_model.service_name != "organizations"
        ):
            raise TypeError("a boto3 organizations client must be provided")

        self._client = client

    def get_client(self) -> OrganizationsClient:
        return cast(OrganizationsClient, self._client)
