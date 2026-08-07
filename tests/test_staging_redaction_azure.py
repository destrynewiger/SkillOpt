"""Tests for the additional Azure secret redaction patterns in staging.

``redact_secrets`` scrubs credential-looking substrings before persisting any
free text to the staging directory. This covers the newly added Azure SAS
signature, storage account key, and connection-string password patterns.
"""
from __future__ import annotations

from skillopt_sleep.staging import redact_secrets


def test_azure_sas_signature_redacted() -> None:
    url = "https://acct.blob.core.windows.net/c/b?sig=abcDEF123%2Bxyz789QQ&se=2026"
    out = redact_secrets(url)
    assert "[REDACTED_SAS_SIG]" in out
    assert "abcDEF123" not in out


def test_storage_account_key_redacted() -> None:
    conn = "DefaultEndpointsProtocol=https;AccountKey=aB3dEfGhIjKlMnOpQrStUvWx==;"
    out = redact_secrets(conn)
    assert "[REDACTED_STORAGE_KEY]" in out
    assert "aB3dEfGhIjKlMnOpQrStUvWx" not in out


def test_quoted_or_spaced_storage_account_keys_redacted() -> None:
    for conn in (
        'AccountKey = "aB3dEfGhIjKlMnOpQrStUvWx=="',
        "AccountKey = {aB3dEfGhIjKlMnOpQrStUvWx==}",
    ):
        out = redact_secrets(conn)
        assert out == "AccountKey = [REDACTED_STORAGE_KEY]"


def test_connection_string_password_redacted() -> None:
    conn = "Server=db;Password=Sup3rSecret!;Database=app"
    out = redact_secrets(conn)
    assert "[REDACTED_DB_PASS]" in out
    assert "Sup3rSecret" not in out
    assert out == "Server=db;Password=[REDACTED_DB_PASS];Database=app"


def test_quoted_connection_string_password_redacted() -> None:
    conn = 'Server=db;Password="Sup3r; Secret!";Database=app'
    out = redact_secrets(conn)
    assert out == "Server=db;Password=[REDACTED_DB_PASS];Database=app"


def test_braced_connection_string_password_redacted() -> None:
    conn = "Server=db;Password={top;secret;value};Database=app"
    out = redact_secrets(conn)
    assert out == "Server=db;Password=[REDACTED_DB_PASS];Database=app"


def test_escaped_connection_string_values_are_fully_redacted() -> None:
    for conn in (
        'Server=db;Password="top""secret";Database=app',
        "Server=db;Password={top}}secret};Database=app",
    ):
        out = redact_secrets(conn)
        assert out == "Server=db;Password=[REDACTED_DB_PASS];Database=app"


def test_generic_secret_redaction_preserves_following_fields() -> None:
    text = "token=top-secret&request=42;status=failed"
    out = redact_secrets(text)
    assert out == "token=[REDACTED]&request=42;status=failed"


def test_quoted_generic_secrets_redacted() -> None:
    for text in (
        'token="opaquevalue123"',
        "api_key='opaquevalue123'",
        "secret={opaque;value;123}",
    ):
        out = redact_secrets(text)
        assert "opaque" not in out
        assert "[REDACTED]" in out


def test_truncated_quoted_secrets_fail_closed() -> None:
    for text in (
        'token="opaquevalue123',
        "api_key='opaquevalue123",
        'Password="Sup3rSecret!',
        'Authorization: Bearer "opaquevalue123',
    ):
        out = redact_secrets(text)
        assert "opaquevalue123" not in out
        assert "Sup3rSecret" not in out
        assert "[REDACTED" in out

    multiline = 'token="opaquevalue123\nrequest failed'
    assert redact_secrets(multiline) == "token=[REDACTED]\nrequest failed"


def test_backslash_escaped_quoted_secrets_are_fully_redacted() -> None:
    for text in (
        'token="top\\"secret";next=ok',
        "api_key='top\\'secret';next=ok",
        'Authorization: Bearer "top\\"secret" next',
    ):
        out = redact_secrets(text)
        assert "top" not in out
        assert "secret" not in out.casefold()
        assert "next" in out


def test_environment_style_secret_names_are_redacted() -> None:
    for text in (
        "AZURE_CLIENT_SECRET=abcdefghijklmnopqrstuvwxyz",
        "AZURE_OPENAI_API_KEY=abcdefghijklmnopqrstuvwxyz",
        "access_token=abcdefghijklmnopqrstuvwxyz",
        "refresh_token=abcdefghijklmnopqrstuvwxyz",
        "SharedAccessKey=abcdefghijklmnopqrstuvwxyz",
        "AWS_SECRET_ACCESS_KEY=abcdefghijklmnopqrstuvwxyz",
    ):
        out = redact_secrets(text)
        assert "abcdefghijklmnopqrstuvwxyz" not in out, text
        assert "[REDACTED]" in out


def test_secret_key_and_camel_case_names_are_redacted() -> None:
    for text in (
        "SECRET_KEY=abcdefghijklmnopqrstuvwxyz",
        "clientSecret=abcdefghijklmnopqrstuvwxyz",
        "serviceAccessToken=abcdefghijklmnopqrstuvwxyz",
    ):
        out = redact_secrets(text)
        assert "abcdefghijklmnopqrstuvwxyz" not in out, text
        assert "[REDACTED]" in out


def test_process_pwd_is_preserved_but_odbc_pwd_is_redacted() -> None:
    assert redact_secrets("PWD=/home/user/project") == "PWD=/home/user/project"
    assert redact_secrets("Pwd=abcdefghijklmnopqrstuvwxyz") == (
        "Pwd=[REDACTED_DB_PASS]"
    )
    connection = "Driver={ODBC Driver};UID=sa;PWD=hunter2;Database=app"
    assert redact_secrets(connection) == (
        "Driver={ODBC Driver};UID=sa;PWD=[REDACTED_DB_PASS];Database=app"
    )


def test_long_alphabetic_bare_token_is_redacted() -> None:
    assert redact_secrets("token abcdefghijklmnopqrstuvwxyz") == (
        "token [REDACTED]"
    )


def test_delimited_bare_secrets_are_redacted() -> None:
    assert redact_secrets("token abc123def, retrying") == (
        "token [REDACTED], retrying"
    )
    assert redact_secrets("password s3cret-value; retrying") == (
        "password [REDACTED]; retrying"
    )


def test_assignment_redaction_preserves_closing_delimiters() -> None:
    assert redact_secrets("retry(token=abc123def)") == (
        "retry(token=[REDACTED])"
    )
    assert redact_secrets("values[api_key=abc123def]") == (
        "values[api_key=[REDACTED]]"
    )
    assert redact_secrets("token=ab}cd") == "token=[REDACTED]"
    assert redact_secrets("token=ab}}cd") == "token=[REDACTED]"
    assert redact_secrets("f(g(token=abc123def))") == (
        "f(g(token=[REDACTED]))"
    )
    assert redact_secrets("[[api_key=abc123def]]") == (
        "[[api_key=[REDACTED]]]"
    )


def test_json_style_secret_assignments_remain_well_formed() -> None:
    text = (
        '{"token":"opaquevalue123",'
        '"api_key":"otheropaque456",'
        '"AZURE_CLIENT_SECRET":"thirdopaque789"}'
    )
    out = redact_secrets(text)
    assert "opaque" not in out
    assert out == (
        '{"token":"[REDACTED]",'
        '"api_key":"[REDACTED]",'
        '"AZURE_CLIENT_SECRET":"[REDACTED]"}'
    )


def test_mapping_keys_drive_recursive_redaction() -> None:
    payload = {
        "token": "opaquevalue123",
        "nested": {
            "access_token": "nestedopaque456",
            "password": "Sup3rSecret!",
            "token_budget": 42,
        },
        "PWD": "/home/user/project",
    }

    out = redact_secrets(payload)

    assert out["token"] == "[REDACTED]"
    assert out["nested"]["access_token"] == "[REDACTED]"
    assert out["nested"]["password"] == "[REDACTED]"
    assert out["nested"]["token_budget"] == 42
    assert out["PWD"] == "/home/user/project"


def test_redaction_is_idempotent_across_supported_secret_forms() -> None:
    samples = (
        "token=[REDACTED]",
        "Password=[REDACTED_DB_PASS]",
        "Pwd=[REDACTED_DB_PASS]",
        "AccountKey=[REDACTED_STORAGE_KEY]",
        "Authorization: Bearer [REDACTED]",
        "retry(token=abc123def)",
        "values[api_key=abc123def]",
        '{"token":"opaquevalue123"}',
    )
    for sample in samples:
        once = redact_secrets(sample)
        assert redact_secrets(once) == once, sample


def test_ordinary_security_prose_is_not_redacted() -> None:
    for text in (
        "token budget exceeded",
        "password reset failed",
        "The token count is 42",
    ):
        assert redact_secrets(text) == text


def test_recurses_into_containers() -> None:
    payload = {"logs": ["ok", "AccountKey=aB3dEfGhIjKlMnOpQrStUvWx=="]}
    out = redact_secrets(payload)
    assert "[REDACTED_STORAGE_KEY]" in out["logs"][1]


def test_plain_text_unchanged() -> None:
    assert redact_secrets("the quick brown fox jumps") == "the quick brown fox jumps"
