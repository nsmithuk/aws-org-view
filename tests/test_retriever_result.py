from aws_org_view import OUMembershipRetrieverResult

def test_ou_membership_result_get_accounts_flattens():
    result = OUMembershipRetrieverResult(
        {
            "r-root": {
                "name": "Organization Root",
                "accounts": [{"Id": "111"}],
                "org_units": {
                    "ou-a": {
                        "name": "OU-A",
                        "accounts": [{"Id": "222"}],
                        "org_units": {
                            "ou-b": {"name": "OU-B", "accounts": [{"Id": "333"}], "org_units": {}},
                            "ou-c": {"name": "OU-C", "accounts": [{"Id": "444"}], "org_units": {}},
                        },
                    }
                },
            }
        }
    )
    assert [a["Id"] for a in result.get_accounts()] == ["111", "222", "333", "444"]
