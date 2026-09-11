#!/usr/bin/env python3
"""Schluesselpaar fuer den Feed-Dienst — erzeugen, ansehen, signieren.

Ed25519, weil der oeffentliche Teil 32 Byte hat: er passt als eine Zeile in
den Quelltext der Integration, und genau dort muss er stehen. Ein Schluessel,
der neben den Daten liegt, prueft nichts.

    python tools/feed_keys.py neu --out feed-private.pem
    python tools/feed_keys.py oeffentlich --key feed-private.pem
    python tools/feed_keys.py signieren --key feed-private.pem datei.json

Der private Teil gehoert **nicht** ins Repo. Im Prober kommt er aus der
Umgebungsvariablen ``FAR_FEED_PRIVATE_KEY`` (der PEM-Text selbst, nicht ein
Pfad) — in GitHub Actions also aus einem Secret.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

ENV_PRIVATE_KEY = "FAR_FEED_PRIVATE_KEY"


def load_private_key(
    pem: str | None = None, path: os.PathLike[str] | str | None = None
) -> ed25519.Ed25519PrivateKey:
    """Privaten Schluessel aus PEM-Text, Datei oder Umgebung holen."""
    if pem is None:
        if path is not None:
            pem = Path(path).read_text(encoding="utf-8")
        else:
            pem = os.environ.get(ENV_PRIVATE_KEY, "")
    if not pem.strip():
        raise SystemExit(
            f"Kein privater Schluessel. Entweder --key <datei> oder {ENV_PRIVATE_KEY} setzen."
        )
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise SystemExit("Der Schluessel ist kein Ed25519-Schluessel.")
    return key


def public_b64(key: ed25519.Ed25519PrivateKey) -> str:
    """Der oeffentliche Teil, wie er in ``feed.FEED_PUBLIC_KEY_B64`` steht."""
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def sign_bytes(key: ed25519.Ed25519PrivateKey, raw: bytes) -> str:
    """Signiere genau diese Bytes; Ergebnis als Base64-Zeile."""
    return base64.b64encode(key.sign(raw)).decode("ascii")


def _neu(args: argparse.Namespace) -> int:
    ziel = Path(args.out)
    if ziel.exists() and not args.force:
        print(f"{ziel} gibt es schon. --force zum Ueberschreiben.", file=sys.stderr)
        return 2

    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    ziel.write_bytes(pem)
    with contextlib.suppress(OSError):  # Windows kennt diese Rechte so nicht
        ziel.chmod(0o600)

    print(f"Privater Schluessel: {ziel}")
    print("  Nicht einchecken. Nicht weitergeben. Verloren heisst: neuer Schluessel,")
    print("  neues Integrations-Release — alte Clients nehmen den Feed sonst nicht mehr an.")
    print()
    print("In custom_components/free_ai_router/feed.py eintragen:")
    print()
    print(f'FEED_PUBLIC_KEY_B64 = "{public_b64(key)}"')
    return 0


def _oeffentlich(args: argparse.Namespace) -> int:
    key = load_private_key(path=args.key)
    print(public_b64(key))
    return 0


def _signieren(args: argparse.Namespace) -> int:
    key = load_private_key(path=args.key)
    quelle = Path(args.datei)
    signatur = sign_bytes(key, quelle.read_bytes())
    ziel = Path(args.out) if args.out else quelle.with_suffix(quelle.suffix + ".sig")
    ziel.write_text(signatur + "\n", encoding="utf-8")
    print(f"{ziel} geschrieben")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    unter = parser.add_subparsers(dest="befehl", required=True)

    neu = unter.add_parser("neu", help="neues Schluesselpaar erzeugen")
    neu.add_argument("--out", default="feed-private.pem")
    neu.add_argument("--force", action="store_true")
    neu.set_defaults(func=_neu)

    oeff = unter.add_parser("oeffentlich", help="oeffentlichen Teil ausgeben")
    oeff.add_argument("--key", default=None, help=f"PEM-Datei (sonst ${ENV_PRIVATE_KEY})")
    oeff.set_defaults(func=_oeffentlich)

    sig = unter.add_parser("signieren", help="eine Datei signieren")
    sig.add_argument("datei")
    sig.add_argument("--key", default=None, help=f"PEM-Datei (sonst ${ENV_PRIVATE_KEY})")
    sig.add_argument("--out", default=None)
    sig.set_defaults(func=_signieren)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
