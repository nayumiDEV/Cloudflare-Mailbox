"""Unit tests for OTP extraction (no database)."""

from app import main as app_module


def test_extracts_hyphenated_otp():
    assert app_module.extract_otp("Your verification code is 123-456") == "123456"
    assert app_module.extract_otp("Code: 12-34-56") == "123456"
    assert app_module.extract_otp("OTP 123 456") == "123456"


def test_ignores_css_hex_colors():
    html = """
    <html><head><style>
      body { color: #333333; background: #ffffff; }
      .btn { color: #ff6600; }
    </style></head>
    <body><p>Welcome</p></body></html>
    """
    text = app_module.html_to_text(html)
    assert "333333" not in text.replace(" ", "")
    assert app_module.extract_otp(text, "Hello") is None
    assert app_module.extract_otp(app_module.html_to_text(html), "Newsletter") is None


def test_otp_not_confused_with_inline_style_color():
    html = '<div style="color:#333333;font-size:14px">Your login code is 847291</div>'
    text = app_module.html_to_text(html)
    assert app_module.extract_otp(text) == "847291"


def test_contextual_otp_beats_random_digits():
    body = "Order #202401 placed. Your security code is 998877."
    assert app_module.extract_otp(body) == "998877"


def test_html_to_text_skips_script_and_style():
    html = "<style>.x{color:#abcdef}</style><script>var x=123456</script><p>Code: 654321</p>"
    text = app_module.html_to_text(html)
    assert "abcdef" not in text.lower()
    assert "123456" not in text
    assert app_module.extract_otp(text) == "654321"


def test_normalize_otp_digits():
    assert app_module.normalize_otp_digits("123-456") == "123456"
    assert app_module.normalize_otp_digits("12") is None
    assert app_module.normalize_otp_digits("123456789") is None


def test_css_false_positive_detector():
    assert app_module.otp_looks_like_css_false_positive(
        "333333", "", '<span style="color:#333333">Hi</span>'
    )
    assert not app_module.otp_looks_like_css_false_positive(
        "847291", "Your code is 847291", "<p>Your code is 847291</p>"
    )


def test_sanitize_keeps_percent_width_and_flex():
    raw = '<table width="100%" style="display:flex;max-width:600px"><tr><td>Hi</td></tr></table>'
    out = app_module.sanitize_email_html(raw, {}, True)
    assert 'width="100%"' in out
    assert "display: flex" in out or "display:flex" in out.replace(" ", "")


def test_sanitize_allows_remote_background_when_enabled():
    raw = '<div style="background-image:url(https://cdn.example.com/a.png)">x</div>'
    on = app_module.sanitize_email_html(raw, {}, True)
    off = app_module.sanitize_email_html(raw, {}, False)
    assert "external-content.duckduckgo.com/iu/?u=" in on
    assert "cdn.example.com" in on
    assert "cdn.example.com" not in off


def test_sanitize_proxies_img_src():
    raw = '<img src="https://cdn.example.com/logo.png" alt="x">'
    on = app_module.sanitize_email_html(raw, {}, True)
    assert 'src="https://external-content.duckduckgo.com/iu/?u=' in on
    assert "cdn.example.com" in on
    # Relative / attachment URLs stay direct (not proxied).
    local = app_module.sanitize_email_html('<img src="/api/attachments/1" alt="x">', {}, True)
    assert 'src="/api/attachments/1"' in local


def test_sanitize_meta_and_link_do_not_swallow_body():
    raw = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        '<link rel="stylesheet" href="style.css">'
        '<title>Test</title></head>'
        '<body><p>Hello NDB OTP</p><div>Code: 889900</div></body></html>'
    )
    cleaned = app_module.sanitize_email_html(raw, {}, False)
    assert "<p>Hello NDB OTP</p>" in cleaned
    assert "Code: 889900" in cleaned


def test_sanitize_cid_mapping_to_data_uri():
    cid_map = {"logo": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="}
    raw = '<p>Company:</p><img src="cid:logo" alt="Logo">'
    cleaned = app_module.sanitize_email_html(raw, cid_map, False)
    assert 'src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="' in cleaned


def test_parse_email_windows_1252_charset():
    raw_email = (
        b"From: sender@example.com\r\n"
        b"To: inbox@example.com\r\n"
        b"Subject: Test Accent\r\n"
        b"Content-Type: text/html; charset=windows-1252\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n\r\n"
        b"<html><body><p>Cafe =E9 and OTP is 654321</p></body></html>"
    )
    res = app_module.parse_email(raw_email)
    assert "Café" in res["body_html"] or "Cafe é" in res["body_html"]
    assert res["otp"] == "654321"

