import sqlite3

import conftest
import services.auth_service as auth_service
import services.common as common


def _create_account(db_path, username, role_names):
    user_id = common.create_user_account(username, "multi-role-password", role_name=role_names[0])
    conn = sqlite3.connect(db_path)
    try:
        for role_name in role_names[1:]:
            conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (?, (SELECT id FROM roles WHERE name = ?))",
                (user_id, role_name),
            )
        conn.commit()
    finally:
        conn.close()
    return user_id


def _authenticate(db_path, username):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return auth_service.authenticate_user(username, "multi-role-password", conn=conn)
    finally:
        conn.close()


def test_primary_role_is_the_highest_privilege_role_held(isolate_test_database):
    db_path = isolate_test_database
    _create_account(db_path, "member_operator", ["member", "operator"])
    _create_account(db_path, "operator_director", ["operator", "tournament_director"])
    _create_account(db_path, "admin_member", ["member", "administrator"])

    member_operator = _authenticate(db_path, "member_operator")
    operator_director = _authenticate(db_path, "operator_director")
    admin_member = _authenticate(db_path, "admin_member")

    assert member_operator["role"] == "operator"
    assert member_operator["roles"] == ("operator", "member")
    assert operator_director["role"] == "tournament_director"
    assert admin_member["role"] == "administrator"


def test_permissions_are_the_union_of_every_held_role(app, isolate_test_database):
    db_path = isolate_test_database
    user_id = _create_account(db_path, "director_operator", ["tournament_director", "operator"])

    with app.test_request_context("/"):
        auth_service.session["user_id"] = user_id
        auth_service.session["auth_version"] = _auth_version(db_path, user_id)
        # data_admin is granted by operator but not by tournament_director.
        assert auth_service.user_has_permission("data_admin")
        assert auth_service.user_has_permission("tournament_admin")
        assert not auth_service.user_has_permission("admin")


def test_member_with_a_staff_role_is_not_redirected_as_member_only(client, isolate_test_database):
    db_path = isolate_test_database
    member_only = _create_account(db_path, "plain_member", ["member"])
    member_and_operator = _create_account(db_path, "staff_member", ["member", "operator"])

    conftest.set_user_session(client, member_only, db_path)
    member_response = client.get("/admin?lang=en")
    conftest.set_user_session(client, member_and_operator, db_path)
    staff_response = client.get("/admin?lang=en")

    assert member_response.status_code == 302
    assert staff_response.status_code == 200


def test_member_only_account_still_lacks_operator_permissions(app, isolate_test_database):
    db_path = isolate_test_database
    user_id = _create_account(db_path, "plain_member_perms", ["member"])

    with app.test_request_context("/"):
        auth_service.session["user_id"] = user_id
        auth_service.session["auth_version"] = _auth_version(db_path, user_id)
        assert auth_service.user_has_permission("results_submitter")
        assert not auth_service.user_has_permission("operator")


def _auth_version(db_path, user_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT auth_version FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    finally:
        conn.close()
