from idor_workbench.views.api import _sync_role_references


def test_role_references_follow_same_credentials_when_role_name_changes():
    old_project = {
        "roles": [{"name": "jim1@juice-sh.op", "username": "jim1@juice-sh.op", "password": "ncc-1701"}]
    }
    project = {
        "roles": [{"name": "normal_user_b", "username": "jim1@juice-sh.op", "password": "ncc-1701"}],
        "endpoints": [{"allowed_roles": ["jim1@juice-sh.op"], "observed_roles": ["jim1@juice-sh.op"]}],
    }

    _sync_role_references(project, old_project)

    assert project["endpoints"][0]["allowed_roles"] == ["normal_user_b"]
    assert project["endpoints"][0]["observed_roles"] == ["normal_user_b"]


def test_role_references_do_not_follow_when_credentials_change():
    old_project = {
        "roles": [{"name": "old_role", "username": "old@example.com", "password": "old-pass"}]
    }
    project = {
        "roles": [{"name": "new_role", "username": "new@example.com", "password": "new-pass"}],
        "endpoints": [{"allowed_roles": ["old_role"], "observed_roles": ["old@example.com"]}],
    }

    _sync_role_references(project, old_project)

    assert project["endpoints"][0]["allowed_roles"] == ["old_role"]
    assert project["endpoints"][0]["observed_roles"] == ["old@example.com"]


def test_role_references_can_recover_from_recorded_source_username():
    project = {
        "roles": [{"name": "normal_user_b", "username": "jim1@juice-sh.op", "password": "ncc-1701"}],
        "endpoints": [{
            "source_username": "jim1@juice-sh.op",
            "allowed_roles": ["stale-display-name"],
            "observed_roles": ["stale-display-name"],
            "discovered_by": ["stale-display-name"],
        }],
    }

    _sync_role_references(project, old_project=None)

    assert "normal_user_b" in project["endpoints"][0]["allowed_roles"]
    assert "normal_user_b" in project["endpoints"][0]["observed_roles"]
    assert project["endpoints"][0]["source_role"] == "normal_user_b"
