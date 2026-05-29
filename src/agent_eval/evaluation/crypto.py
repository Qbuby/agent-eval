"""Symmetric encryption for at-rest secrets (evaluator-provider API keys, etc.).

Uses ``cryptography.fernet`` (AES-128-CBC + HMAC-SHA256, base64-encoded)
because it is opinionated, includes auth tags, and ships in a vetted lib.

Key material lives in ``settings.security.fernet_key``. If the key is
unset, ``encrypt_secret`` raises so we don't silently store plaintext.
``decrypt_secret`` returns ``None`` on tampered or rotated ciphertexts so
callers can prompt the user to re-enter the secret without crashing.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from agent_eval.config import settings

logger = logging.getLogger(__name__)


class CryptoUnavailable(RuntimeError):
    """Raised when encryption is requested but no fernet_key is configured."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    key = settings.security.fernet_key
    if not key:
        raise CryptoUnavailable(
            "SECURITY_FERNET_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and add to .env."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> bytes:
    """Encrypt a secret string. Returns raw bytes suitable for BYTEA storage.

    Raises ``CryptoUnavailable`` if the fernet key is not configured.
    """
    if plaintext == "":
        return b""
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes | None) -> str | None:
    """Decrypt a stored ciphertext. Returns ``None`` if blob is empty,
    the key has rotated, or the ciphertext is corrupted.
    """
    if not ciphertext:
        return None
    try:
        return _cipher().decrypt(bytes(ciphertext)).decode("utf-8")
    except InvalidToken:
        logger.warning("decrypt_secret: ciphertext does not match current fernet key")
        return None
    except CryptoUnavailable:
        return None


def mask_secret(plaintext: str | None, *, visible_tail: int = 4) -> str:
    """Render a secret for UI display: ``sk-•••••••••wxyz``."""
    if not plaintext:
        return ""
    if len(plaintext) <= visible_tail:
        return "•" * len(plaintext)
    return f"{plaintext[:3]}{'•' * 8}{plaintext[-visible_tail:]}"
