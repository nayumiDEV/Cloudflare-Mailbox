import pytest
from app import main as app_module
from conftest import create_user, ingest


def test_normalize_email_with_display_names_and_brackets():
    assert app_module.normalize_email("user@example.com") == "user@example.com"
    assert app_module.normalize_email("<user@example.com>") == "user@example.com"
    assert app_module.normalize_email("John Doe <user@example.com>") == "user@example.com"
    assert app_module.normalize_email("  <HELLO@EXAMPLE.COM>  ") == "hello@example.com"


def test_validate_rule_types():
    assert app_module.validate_rule("prefix", "dev-") == "dev-"
    assert app_module.validate_rule("domain", "@example.com") == "example.com"
    assert app_module.validate_rule("domain", "test.org") == "test.org"
    assert app_module.validate_rule("catch_all", "*") == "*"
    assert app_module.validate_rule("catch_all", "") == "*"
    assert app_module.validate_rule("catch_all", "*@company.com") == "*@company.com"
    assert app_module.validate_rule("regex", "^[0-9]+@.*") == "^[0-9]+@.*"

    with pytest.raises(Exception):
        app_module.validate_rule("unknown", "pattern")
    with pytest.raises(Exception):
        app_module.validate_rule("domain", "not-a-domain")
    with pytest.raises(Exception):
        app_module.validate_rule("regex", "[unclosed")


def test_rule_matches():
    prefix_r = {"rule_type": "prefix", "pattern": "team-*"}
    domain_r = {"rule_type": "domain", "pattern": "company.com"}
    catchall_global_r = {"rule_type": "catch_all", "pattern": "*"}
    catchall_dom_r = {"rule_type": "catch_all", "pattern": "*@sub.company.com"}
    regex_r = {"rule_type": "regex", "pattern": r"^otp-\d+@"}

    # Prefix
    assert app_module.rule_matches(prefix_r, "team-alpha@other.com") is True
    assert app_module.rule_matches(prefix_r, "other-team@other.com") is False

    # Domain
    assert app_module.rule_matches(domain_r, "alice@company.com") is True
    assert app_module.rule_matches(domain_r, "alice@sub.company.com") is True
    assert app_module.rule_matches(domain_r, "alice@notcompany.com") is False

    # Catch-all global
    assert app_module.rule_matches(catchall_global_r, "random-user@anywhere.net") is True

    # Catch-all domain
    assert app_module.rule_matches(catchall_dom_r, "test@sub.company.com") is True
    assert app_module.rule_matches(catchall_dom_r, "test@other.com") is False

    # Regex
    assert app_module.rule_matches(regex_r, "otp-123456@service.io") is True
    assert app_module.rule_matches(regex_r, "otp-abc@service.io") is False


def test_catch_all_auto_provisions_mailbox(client, admin):
    admin_c = admin["c"]
    csrf = admin["csrf"]

    # 1. Create a regular user
    bob = create_user("bob_catchall")
    bob_pid = bob["public_id"]

    # 2. Admin creates a domain rule for "mycatchall.com" -> bob_catchall
    rule_res = admin_c.post(
        "/api/admin/rules",
        json={
            "rule_type": "domain",
            "pattern": "mycatchall.com",
            "target_public_id": bob_pid,
            "description": "Route all mycatchall.com emails to Bob",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert rule_res.status_code == 200, rule_res.text

    # 3. Email arrives via worker ingest for "hello-world-123@mycatchall.com"
    ingest_res = ingest(
        client,
        to="hello-world-123@mycatchall.com",
        subject="Welcome to Catch-All",
        otp_hint="789456",
    )
    assert ingest_res.status_code == 201, ingest_res.text
    assert ingest_res.json()["otp"] == "789456"

    # 4. Login as Bob and check visible mailboxes and messages
    bob_login = client.post(
        "/api/login",
        json={"username": "bob_catchall", "password": "password-for-tests"},
    )
    assert bob_login.status_code == 200
    bob_csrf = bob_login.json()["csrf"]

    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    mailboxes = dashboard.json()["mailboxes"]
    assert any(m["address"] == "hello-world-123@mycatchall.com" for m in mailboxes)

    # Bob can view messages for this mailbox
    box_id = [m["id"] for m in mailboxes if m["address"] == "hello-world-123@mycatchall.com"][0]
    msgs = client.get(f"/api/messages?mailbox_id={box_id}")
    assert msgs.status_code == 200
    assert len(msgs.json()["items"]) == 1
    assert msgs.json()["items"][0]["otp"] == "789456"


def test_global_catch_all_rule(client, admin):
    from conftest import create_user, ingest
    admin_c = admin["c"]
    csrf = admin["csrf"]

    carol = create_user("carol_catchall")
    carol_pid = carol["public_id"]

    # Admin creates a global catch-all rule (*) -> Carol
    rule_res = admin_c.post(
        "/api/admin/rules",
        json={
            "rule_type": "catch_all",
            "pattern": "*",
            "target_public_id": carol_pid,
            "description": "Global catch-all",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert rule_res.status_code == 200

    # Ingest email to arbitrary address
    ingest_res = ingest(
        client,
        to="random-str-999@different-domain.com",
        subject="Any Subject",
        otp_hint="654321",
    )
    assert ingest_res.status_code == 201

    # Carol logs in and sees it
    client.post("/api/login", json={"username": "carol_catchall", "password": "password-for-tests"})
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    addresses = [m["address"] for m in dashboard.json()["mailboxes"]]
    assert "random-str-999@different-domain.com" in addresses


def test_admin_rules_test_endpoint(admin):
    from conftest import create_user
    admin_c = admin["c"]
    csrf = admin["csrf"]

    u = create_user("rule_tester")
    admin_c.post(
        "/api/admin/rules",
        json={
            "rule_type": "domain",
            "pattern": "testsuite.io",
            "target_public_id": u["public_id"],
        },
        headers={"X-CSRF-Token": csrf},
    )

    res = admin_c.get("/api/admin/rules/test?address=incoming@testsuite.io")
    assert res.status_code == 200
    data = res.json()
    assert data["exact_override"] is False
    assert len(data["matches"]) >= 1
    assert data["matches"][0]["pattern"] == "testsuite.io"

