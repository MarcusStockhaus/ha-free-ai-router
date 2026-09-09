"""Testhilfen.

Die Provider- und Model-Objekte werden hier direkt gebaut, nicht ueber
``Provider.parse``. Das umgeht bewusst die Host-Allowlist: ein Test-Server
laeuft auf ``127.0.0.1``, und die Allowlist soll genau das im Produktivbetrieb
verhindern. Ihre eigene Wirkung wird in ``test_registry.py`` geprueft.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from custom_components.free_ai_router.registry import (  # noqa: E402
    Capabilities,
    Limits,
    Model,
    Onboarding,
    Pricing,
    Provider,
)

ONBOARDING = Onboarding(
    signup_url="https://example.invalid/keys",
    steps_de=("anmelden", "Key kopieren"),
    data_note_de="Testanbieter, keine echten Daten.",
    credit_card_required=False,
    summary_de="Nur fuer Tests.",
)


def make_model(
    model_id: str,
    provider_id: str,
    *,
    profiles: tuple[str, ...] = ("schnell",),
    vision: bool = False,
    tools: bool = True,
    structured_output: bool = True,
    context_tokens: int = 100_000,
    rpm: int | None = None,
    rpd: int | None = None,
    tpm: int | None = None,
    latency_class: str = "normal",
) -> Model:
    return Model(
        id=model_id,
        provider_id=provider_id,
        profiles=profiles,
        capabilities=Capabilities(
            vision=vision,
            tools=tools,
            structured_output=structured_output,
            context_tokens=context_tokens,
        ),
        limits=Limits(rpm=rpm, rpd=rpd, tpm=tpm),
        pricing=Pricing(),
        latency_class=latency_class,
    )


def make_provider(
    provider_id: str,
    models: tuple[Model, ...],
    *,
    api_style: str = "openai_compatible",
    base_url: str = "https://example.invalid/v1",
    auth_type: str = "bearer",
    auth_name: str = "",
    limits_scope: str = "per_model",
    daily_reset_timezone: str = "UTC",
    preference: int = 50,
) -> Provider:
    return Provider(
        id=provider_id,
        name=provider_id.replace("_", " ").title(),
        api_style=api_style,
        base_url=base_url,
        auth_type=auth_type,
        auth_name=auth_name,
        onboarding=ONBOARDING,
        models=models,
        limits_scope=limits_scope,
        daily_reset_timezone=daily_reset_timezone,
        preference=preference,
    )


@pytest.fixture
def free_availability():
    """Ledger-Ersatz: alles frei."""
    from custom_components.free_ai_router.ledger import Availability

    return lambda _provider, _model: Availability(ok=True)
