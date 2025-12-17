import pytest

from aws_org_view import AwsOrgView
from tests.fakes.organizations import FakeOrganizationsClient, FakeOrganizationsClientProvider


@pytest.fixture
def org_client() -> FakeOrganizationsClient:
    return FakeOrganizationsClient()


@pytest.fixture
def provider(org_client: FakeOrganizationsClient) -> FakeOrganizationsClientProvider:
    return FakeOrganizationsClientProvider(org_client)


@pytest.fixture
def view(provider: FakeOrganizationsClientProvider) -> AwsOrgView:
    return AwsOrgView(client=provider, cache_ttl=3600, cache_maxsize=32)
