"""Knowing before a subscription token runs out.

`claude setup-token` mints a one-year token, and Anthropic's own
documentation warns that a session running unattended "stops making
progress once the credential expires and can't recover until you sign in
again". LabDog's scheduled checks are exactly that kind of session, so
without this the first sign of an expired token is nightly reports
quietly stopping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ai.models import AIProvider
from app.ai.providers.factory import uses_subscription
from app.ai.schemas import SUBSCRIPTION_TOKEN_LIFETIME, credential_expiry, provider_to_response


def _provider(provider_type: str, *, set_at: datetime | None) -> AIProvider:
    """An unpersisted provider row.

    Every non-nullable column is set explicitly: SQLAlchemy applies column
    defaults at flush, so an object built in memory has ``None`` in all of
    them and would fail response validation for reasons unrelated to what
    is being tested here.
    """
    return AIProvider(
        id=1,
        name="p",
        provider_type=provider_type,
        model="m",
        encrypted_api_key=b"x" if set_at else None,
        credential_set_at=set_at,
        verify_ssl=True,
        max_tokens=4096,
        temperature=0.0,
        is_default=False,
        input_cost_per_mtok=0.0,
        output_cost_per_mtok=0.0,
        monthly_budget=0.0,
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestWhichBackendsExpire:
    @pytest.mark.parametrize("provider_type", ["claude_cli", "claude_agent"])
    def test_a_subscription_backend_has_an_expiry(self, provider_type: str) -> None:
        set_at = datetime.now(UTC)
        provider = _provider(provider_type, set_at=set_at)
        assert uses_subscription(provider) is True
        assert credential_expiry(provider) == set_at + SUBSCRIPTION_TOKEN_LIFETIME

    @pytest.mark.parametrize("provider_type", ["anthropic", "openai_compat"])
    def test_an_api_key_backend_has_none(self, provider_type: str) -> None:
        """API keys do not expire on a schedule, so inventing a date for
        them would be a warning nobody should act on."""
        provider = _provider(provider_type, set_at=datetime.now(UTC))
        assert uses_subscription(provider) is False
        assert credential_expiry(provider) is None

    def test_an_unset_credential_has_none(self) -> None:
        """Covers both a provider with no stored token and one whose token
        predates this being recorded."""
        assert credential_expiry(_provider("claude_agent", set_at=None)) is None


class TestTheLifetimeIsAnAssumption:
    def test_it_matches_the_documented_year(self) -> None:
        """The token is opaque, so the expiry cannot be read from it. This
        is Anthropic's documented lifetime, and it is useful precisely
        because the alternative is no warning at all — if the lifetime
        changes, this constant is the one place to change."""
        assert SUBSCRIPTION_TOKEN_LIFETIME == timedelta(days=365)


class TestItReachesTheResponse:
    def test_the_api_reports_both_fields(self) -> None:
        set_at = datetime(2026, 1, 1, tzinfo=UTC)
        response = provider_to_response(_provider("claude_agent", set_at=set_at))
        assert response.uses_subscription is True
        assert response.credential_expires_at == set_at + SUBSCRIPTION_TOKEN_LIFETIME

    def test_an_api_key_provider_reports_no_expiry(self) -> None:
        response = provider_to_response(_provider("anthropic", set_at=datetime.now(UTC)))
        assert response.uses_subscription is False
        assert response.credential_expires_at is None


class TestTheCredentialTimestampTracksTheKeyNotTheRow:
    """``updated_at`` moves whenever any field is edited, so it cannot
    stand in for when the credential itself was written."""

    async def test_it_is_set_when_a_key_is_stored(self, superuser_client, db, ai_provider) -> None:
        resp = await superuser_client.patch(
            f"/api/ai/providers/{ai_provider.id}", json={"api_key": "sk-test-123"}
        )
        assert resp.status_code == 200
        await db.refresh(ai_provider)
        assert ai_provider.credential_set_at is not None

    async def test_editing_another_field_leaves_it_alone(
        self, superuser_client, db, ai_provider
    ) -> None:
        await superuser_client.patch(
            f"/api/ai/providers/{ai_provider.id}", json={"api_key": "sk-test-123"}
        )
        await db.refresh(ai_provider)
        first = ai_provider.credential_set_at

        await superuser_client.patch(
            f"/api/ai/providers/{ai_provider.id}", json={"model": "something-else"}
        )
        await db.refresh(ai_provider)
        assert ai_provider.credential_set_at == first

    async def test_clearing_the_key_clears_it(self, superuser_client, db, ai_provider) -> None:
        """Otherwise an emptied provider keeps reporting an expiry for a
        credential it no longer holds."""
        await superuser_client.patch(
            f"/api/ai/providers/{ai_provider.id}", json={"api_key": "sk-test-123"}
        )
        await superuser_client.patch(f"/api/ai/providers/{ai_provider.id}", json={"api_key": ""})
        await db.refresh(ai_provider)
        assert ai_provider.credential_set_at is None
