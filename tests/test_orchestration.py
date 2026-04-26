"""Tests for end-to-end approval orchestration (execute_publish + dispatch wiring)."""

from approval_server import sanitize_error

# ---- sanitize_error (T9) ----


def test_sanitize_error_redacts_secrets_and_truncates():
    """T9: Bearer tokens, internal URLs redacted; output <= 200 chars."""
    raw = (
        "Bearer sk-live-abc123 failed at "
        "https://internal.corp/api/v1/thing "
        "with token=xoxb-secret-value and "
        "https://medium.com/p/ok should stay "
        + "x" * 500
    )
    result = sanitize_error(raw)
    assert "sk-live-abc123" not in result
    assert "Bearer" not in result
    assert "token=xoxb" not in result
    assert "internal.corp" not in result
    assert "[REDACTED]" in result
    assert "[URL_REDACTED]" in result
    # medium.com URLs should NOT be redacted
    assert "medium.com" in result
    assert len(result) <= 200
