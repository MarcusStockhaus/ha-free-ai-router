"""Konstanten der Integration.

Dieses Modul importiert bewusst nichts aus ``homeassistant`` — es wird auch
vom eigenstaendigen Probe-CLI (``tools/probe_cli.py``) benutzt.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "free_ai_router"

# Fest verdrahtete Entity-Profile (Phase 1, keine benutzerdefinierten Profile).
PROFILE_SCHNELL: Final = "schnell"
PROFILE_VISION: Final = "vision"
PROFILE_REASONING: Final = "reasoning"
PROFILES: Final = (PROFILE_SCHNELL, PROFILE_VISION, PROFILE_REASONING)

PROFILE_LABELS_DE: Final = {
    PROFILE_SCHNELL: "Schnell",
    PROFILE_VISION: "Bildanalyse",
    PROFILE_REASONING: "Reasoning",
}

# Was ein Profil vom Modell mindestens verlangt. Der Router filtert danach.
#
# Structured Output steht hier bewusst *nicht*: es wird nur verlangt, wenn der
# Aufruf tatsaechlich ein Schema mitbringt. Sonst fielen Modelle heraus, die
# fuer freie Antworten gut sind — Googles Gemma etwa nimmt Bilder an, kann aber
# kein responseSchema.
PROFILE_REQUIREMENTS: Final = {
    PROFILE_SCHNELL: {"vision": False, "tools": False},
    PROFILE_VISION: {"vision": True, "tools": False},
    PROFILE_REASONING: {"vision": False, "tools": False},
}

# Unterstuetzte API-Dialekte.
API_STYLE_OPENAI: Final = "openai_compatible"
API_STYLE_ANTHROPIC: Final = "anthropic"
API_STYLE_GOOGLE: Final = "google"
API_STYLES: Final = (API_STYLE_OPENAI, API_STYLE_ANTHROPIC, API_STYLE_GOOGLE)

CONF_PROVIDER: Final = "provider"
CONF_API_KEY: Final = "api_key"
CONF_CAPABILITIES: Final = "capabilities"
CONF_MODELS: Final = "models"

STORAGE_KEY_LEDGER: Final = f"{DOMAIN}.ledger"
STORAGE_VERSION_LEDGER: Final = 1

# Zeitbudget fuer einen einzelnen Fähigkeitstest.
PROBE_TIMEOUT_S: Final = 45.0
# Zeitbudget fuer einen produktiven Aufruf.
REQUEST_TIMEOUT_S: Final = 90.0
