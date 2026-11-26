"""Explicit macOS Keychain storage for provider credentials."""

import hashlib
import json
import sys

from runtime import DATA_DIR


SERVICE = "com.my-ai-agent.providers." + hashlib.sha256(
    str(DATA_DIR.resolve()).encode()
).hexdigest()[:20]


class KeychainError(Exception):
    pass


def keychain_supported():
    return sys.platform == "darwin"


def _backend():
    if not keychain_supported():
        return None

    try:
        from keyring.backends.macOS import Keyring
        return Keyring()
    except Exception:
        raise KeychainError(
            "macOS Keychain is unavailable."
        ) from None


def read_saved_credential(provider):
    backend = _backend()

    if backend is None:
        return None

    try:
        raw = backend.get_password(SERVICE, provider)

        if raw is None:
            return None

        record = json.loads(raw)

        if (
            not isinstance(record, dict)
            or not isinstance(record.get("key"), str)
            or not record["key"]
            or record.get("state") not in {
                "verified", "unverified"
            }
        ):
            raise ValueError("Invalid credential record")

        return record
    except Exception:
        raise KeychainError(
            "Could not read the saved credential. "
            "Check whether your Keychain is unlocked."
        ) from None


def save_saved_credential(provider, key, state):
    backend = _backend()

    if backend is None:
        raise KeychainError(
            "Persistent credential storage is currently "
            "supported only on macOS."
        )

    if (
        not isinstance(key, str)
        or not key
        or state not in {"verified", "unverified"}
    ):
        raise KeychainError("Invalid credential record.")

    try:
        backend.set_password(
            SERVICE,
            provider,
            json.dumps({"key": key, "state": state}),
        )
    except Exception:
        raise KeychainError(
            "Could not save the credential to Keychain."
        ) from None


def delete_saved_credential(provider):
    backend = _backend()

    if backend is None:
        return

    try:
        if backend.get_password(SERVICE, provider) is not None:
            backend.delete_password(SERVICE, provider)
    except Exception:
        raise KeychainError(
            "Could not remove the saved credential."
        ) from None
