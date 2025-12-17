# aws-org-view

A small Python helper library that provides a **high-level, cached view of AWS Organizations**.

It is designed to be **aggressively cache-heavy and lazy by default**:

- Results from AWS Organizations APIs are stored in TTL caches to **minimise repeated API calls**
- Queries only perform the **minimum number of API calls required** to resolve the specific question being asked (for example, stopping parent traversal as soon as a match is found)

It wraps the low-level AWS Organizations API calls (via `boto3`) to make common “org structure” queries easier, including:

- Checking whether an **account belongs under** a given OU/root (or matches any ID in a “haystack”)
- Building a **nested OU → accounts hierarchy tree**, optionally only one level deep
- Reducing repeated API calls with **TTL caches** (helpful when walking parent chains or listing lots of OUs/accounts)

---

## Install

```shell
pip install aws-org-view
```

---

## Quickstart

```python
from aws_org_view import AwsOrgView

org = AwsOrgView()  # uses boto3.client("organizations") by default

# Example: check if account "123456789012" is within ou-abcd-xyz987 or ou-abcd-wft745 - either directly or in one of their descendant OUs.
found = org.account_in_haystack(
    account_id="123456789012",
    haystack=["ou-abcd-xyz987", "ou-abcd-wft745"],  # OUs, roots, and/or account IDs
)
print(found)

# Example: build a full OU hierarchy (root -> all descendants)
tree = org.get_ou_hierarchy()
print(tree)
```

---

## What `AwsOrgView` does

### Caching

`AwsOrgView` maintains several `cachetools.TTLCache` instances to avoid repeated AWS calls within a time window:

- Parent lookups (`list_parents`)
- Organization root ID (`list_roots`)
- OU descriptions (`describe_organizational_unit`)
- Accounts under a parent (`list_accounts_for_parent`)
- Child OUs under a parent (`list_organizational_units_for_parent`)

You can control cache size and TTL:

```python
org = AwsOrgView(cache_ttl=900, cache_maxsize=1024)  # 15 min TTL
```

---

## Using `account_in_haystack()`

### Purpose

`account_in_haystack()` answers:

> “Does this account appear in (or sit underneath) any ID in this set of IDs?”

It walks **upwards** from the account:

```
account -> parent OU -> ... -> root
```

At each step it checks if the current ID is present in your haystack.

### Signature

```python
def account_in_haystack(
    self,
    account_id: str,
    haystack: set[str] | list[str],
    require_direct_descendant: bool = False,
) -> bool:
```

### Examples

#### 1) Account is directly listed in the haystack

```python
org.account_in_haystack(
    account_id="123456789012",
    haystack=["123456789012"],
)
# -> True
```

#### 2) Account is under an OU in the haystack

```python
org.account_in_haystack(
    account_id="123456789012",
    haystack=["ou-abcd-xyz987"],
)
# -> True if that OU is in the account’s parent chain
```

#### 3) Account is somewhere under the org root in the haystack

```python
org.account_in_haystack(
    account_id="123456789012",
    haystack=["r-a1b2"],
)
```

#### 4) Only count **direct** children of a target OU/root

If `require_direct_descendant=True`, the function only checks:

- the account itself, and
- its **immediate parent**

This is useful if you only consider an account “in scope” when it is directly attached to a given OU/root (not nested multiple OUs deep).

```python
org.account_in_haystack(
    account_id="123456789012",
    haystack=["ou-abcd-xyz987"],
    require_direct_descendant=True,
)
```

### Notes / behavior

- `haystack` can be a `list` or a `set`; lists are converted to a `set`.
- The code limits traversal depth to **6 checks** (AWS supports up to 5 OU levels plus the root).
- If AWS returns anything other than exactly one parent for an entity, a `ParentResolutionError` is raised.

---

## Using `get_ou_hierarchy()`

### Purpose

`get_ou_hierarchy()` builds a **nested dictionary** representing:

- an OU/root node’s name
- its **direct accounts**
- its **child OUs**
- recursively, the children of those OUs (unless you request only one level)

### Signature

```python
def get_ou_hierarchy(
    self,
    parent_id: str | None = None,
    direct_descendants_only: bool = False
) -> OUMembershipRetrieverResult:
```

### Examples

#### 1) Full org tree from the root

```python
org = AwsOrgView()
tree = org.get_ou_hierarchy()  # parent_id=None => use org root
```

#### 2) Tree rooted at a specific OU

```python
tree = org.get_ou_hierarchy(parent_id="ou-abcd-xyz987")
```

#### 3) Only include immediate child OUs (no recursion)

```python
tree = org.get_ou_hierarchy(direct_descendants_only=True)
```

With `direct_descendants_only=True`, the output includes:

- `accounts` at the parent node
- immediate `org_units` entries with only their `name`

It will **not** recursively populate grandchildren OUs.

### Returned structure

The return value is an `OUMembershipRetrieverResult`, which is a `dict` wrapper. The top-level dict has a **single key**: the root ID you requested (OU ID or root ID). That key maps to a node shaped like:

```python
{
  "<parent_id>": {
    "name": "<friendly name>",
    "accounts": [ ...accounts directly under this parent... ],
    "org_units": {
      "<child_ou_id>": {
        "name": "<child ou name>",
        "accounts": [...],
        "org_units": {...}
      },
      ...
    }
  }
}
```

### Getting a flat list of all accounts

`OUMembershipRetrieverResult` provides:

```python
accounts = tree.get_accounts()
```

That recursively walks the tree and returns a single flat list of account objects.

---

## Using a custom client

There are two supported customisation styles:

1) Pass a pre-built **boto3 Organizations client**  
2) Pass a **client provider** (recommended when you need refresh/assume-role logic)

### 1) Pass a boto3 Organizations client

```python
import boto3
from aws_org_view import AwsOrgView

client = boto3.client("organizations")
org = AwsOrgView(client=client)
```

### 2) Pass a custom `OrganizationsClientProvider`

The library defines a runtime-checkable protocol:

```python
class OrganizationsClientProvider(Protocol):
    def get_client(self) -> OrganizationsClient: ...
```

Your provider can do anything internally (assume role, refresh credentials, add custom config), as long as it returns a valid Organizations client from `get_client()`.

#### Example: provider using a named AWS profile

```python
import boto3
from aws_org_view import AwsOrgView

class ProfileOrganizationsClientProvider:
    def __init__(self, profile_name: str):
        self._session = boto3.Session(profile_name=profile_name)

    def get_client(self):
        return self._session.client("organizations")

org = AwsOrgView(client=ProfileOrganizationsClientProvider("prod-admin"))
```

#### Example: provider that assumes a role

```python
import boto3
from aws_org_view import AwsOrgView

class AssumeRoleOrganizationsClientProvider:
    def __init__(self, role_arn: str, session_name: str = "aws-org-view"):
        self._sts = boto3.client("sts")
        self._role_arn = role_arn
        self._session_name = session_name

    def get_client(self):
        creds = self._sts.assume_role(
            RoleArn=self._role_arn,
            RoleSessionName=self._session_name,
        )["Credentials"]

        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
        return session.client("organizations")

org = AwsOrgView(
    client=AssumeRoleOrganizationsClientProvider(
        "arn:aws:iam::123456789012:role/OrgReadRole"
    )
)
```

## Development

Create (and remove if needed) the Hatch dev environment.
```bash
hatch env remove dev
hatch env create dev
```

Run tests: `hatch run dev:fmt`

Run code linting: `hatch run dev:pytest`

Run code type checking: `hatch run dev:typing`

## License

`aws-org-view` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.

---

*Python written by humans. English written by AI.*
