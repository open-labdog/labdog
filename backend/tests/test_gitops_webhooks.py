"""Tests for GitOps webhook signature verification — pure unit tests, no DB."""

import hashlib
import hmac
import inspect

import pytest

from app.api.webhooks import _verify_gitea_signature, _verify_github_signature, _verify_gitlab_token

pytestmark = pytest.mark.integration


class TestWebhookSignatures:
    def test_github_hmac_valid(self):
        """Compute real HMAC-SHA256, verify _verify_github_signature returns True."""
        secret = "test-webhook-secret"
        body = b'{"ref":"refs/heads/main","after":"abc123"}'
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_github_signature(body, secret, sig) is True

    def test_github_hmac_invalid(self):
        """Wrong signature returns False."""
        secret = "test-webhook-secret"
        body = b'{"ref":"refs/heads/main","after":"abc123"}'
        assert _verify_github_signature(body, secret, "sha256=deadbeef") is False

    def test_github_missing_signature(self):
        """None signature returns False."""
        assert _verify_github_signature(b"body", "secret", None) is False

    def test_github_wrong_prefix(self):
        """Signature without sha256= prefix returns False."""
        secret = "secret"
        body = b"data"
        raw_hmac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_github_signature(body, secret, raw_hmac) is False

    def test_gitlab_token_valid(self):
        """Correct token matches via compare_digest."""
        secret = "my-gitlab-secret-token"
        assert _verify_gitlab_token(secret, secret) is True

    def test_gitlab_token_invalid(self):
        """Wrong token returns False."""
        assert _verify_gitlab_token("correct-secret", "wrong-token") is False

    def test_gitlab_token_missing(self):
        """None token returns False."""
        assert _verify_gitlab_token("secret", None) is False

    def test_gitea_hmac_valid(self):
        """Compute real HMAC-SHA256, verify _verify_gitea_signature returns True."""
        secret = "gitea-secret"
        body = b'{"ref":"refs/heads/main"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_gitea_signature(body, secret, sig) is True

    def test_gitea_hmac_invalid(self):
        """Wrong signature returns False."""
        assert _verify_gitea_signature(b"body", "secret", "badhex") is False

    def test_compare_digest_used(self):
        """All signature verification functions use hmac.compare_digest (timing-safe)."""
        for func in (_verify_github_signature, _verify_gitea_signature, _verify_gitlab_token):
            source = inspect.getsource(func)
            assert "compare_digest" in source, (
                f"{func.__name__} must use hmac.compare_digest for timing-safe comparison"
            )
