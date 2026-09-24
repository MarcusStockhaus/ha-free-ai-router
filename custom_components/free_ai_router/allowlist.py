"""Fest eincompilierte Host-Allowlist.

Anbieterdateien sind YAML und sollen per Pull Request ohne Python-Kenntnisse
aenderbar sein. Sie duerfen damit Modelle, Limits und Texte aendern — aber
niemals, *wohin* Daten fliessen. Deshalb steht die Liste erlaubter Hosts hier
im Python-Code und nicht in den Datendateien. Eine manipulierte Anbieterdatei
koennte sonst Kamerabilder umleiten, ohne dass es beim Durchsehen des Diffs
auffaellt.

Aenderungen an dieser Liste sind ein Code-Review-Vorgang, kein Datenupdate.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Hosts, die eine Registry-Datei als ``base_url`` verwenden darf.
# Bewusst breiter als die vier mitgelieferten Anbieter: auch schon gepruefte,
# aber derzeit nicht aufgenommene Anbieter stehen hier, damit ihre Wiederaufnahme
# eine reine Datenaenderung bleibt.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "generativelanguage.googleapis.com",  # Google AI Studio
        "api.groq.com",                       # Groq
        "opencode.ai",                        # OpenCode Zen
        "openrouter.ai",                      # OpenRouter
        "api.cerebras.ai",                    # Cerebras
        "api.mistral.ai",                     # Mistral
        "api.anthropic.com",                  # Anthropic (api_style-Referenz)
    }
)


class HostNotAllowedError(ValueError):
    """Eine Registry-Datei nennt einen Endpunkt ausserhalb der Allowlist."""


def check_url(url: str, *, source: str = "<registry>") -> str:
    """Pruefe eine URL gegen die Allowlist und gib sie normalisiert zurueck.

    Verlangt wird HTTPS, ein Host aus :data:`ALLOWED_HOSTS`, kein
    Benutzer-Teil und kein abweichender Port. Alles andere ist ein Fehler,
    nicht eine Warnung.
    """
    parts = urlsplit(url)

    if parts.scheme != "https":
        raise HostNotAllowedError(
            f"{source}: nur https erlaubt, gefunden {parts.scheme!r} in {url!r}"
        )
    if parts.username or parts.password:
        raise HostNotAllowedError(f"{source}: Benutzerdaten in der URL sind nicht erlaubt")
    if parts.port not in (None, 443):
        raise HostNotAllowedError(f"{source}: abweichender Port {parts.port} in {url!r}")

    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise HostNotAllowedError(
            f"{source}: Host {host!r} steht nicht in der Allowlist "
            f"(erlaubt: {', '.join(sorted(ALLOWED_HOSTS))})"
        )

    return url.rstrip("/")


def is_allowed(url: str) -> bool:
    """Wie :func:`check_url`, aber als Praedikat."""
    try:
        check_url(url)
    except HostNotAllowedError:
        return False
    return True
