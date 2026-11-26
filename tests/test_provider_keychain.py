import os
import unittest

from unittest.mock import Mock, patch
from types import SimpleNamespace
from starlette.datastructures import URL
from fastapi import HTTPException

import provider_keychain as keychain

from providers import (
    provider_key,
    credential_status,
    SESSION_PROVIDER_KEYS,
    SESSION_PROVIDER_VALIDATION,
)

from app import connect_provider, disconnect_provider


class KeychainTests(unittest.TestCase):

    def setUp(self):
        self.request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            url=URL("http://127.0.0.1:8000"),
            headers={"origin": "http://127.0.0.1:8000"},
        )

        for mapping in (
            SESSION_PROVIDER_KEYS,
            SESSION_PROVIDER_VALIDATION,
        ):
            patcher = patch.dict(mapping, {}, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)

        patcher = patch.dict(
            os.environ,
            {"GROQ_API_KEY": "environment-key"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("provider_keychain._backend")
    def test_keychain_roundtrip(self, backend):
        store = {}

        backend.return_value.get_password.side_effect = (
            lambda service, name: store.get((service, name))
        )
        backend.return_value.set_password.side_effect = (
            lambda service, name, value:
            store.__setitem__((service, name), value)
        )
        backend.return_value.delete_password.side_effect = (
            lambda service, name: store.pop((service, name))
        )

        keychain.save_saved_credential(
            "groq", "test-key", "verified"
        )

        self.assertEqual(
            keychain.read_saved_credential("groq"),
            {"key": "test-key", "state": "verified"},
        )

        keychain.delete_saved_credential("groq")
        self.assertIsNone(
            keychain.read_saved_credential("groq")
        )

    @patch("providers.read_saved_credential")
    def test_credential_priority(self, read):
        read.return_value = {
            "key": "saved-key", "state": "verified"
        }

        self.assertEqual(provider_key("groq"), "saved-key")

        SESSION_PROVIDER_KEYS["groq"] = "session-key"
        self.assertEqual(provider_key("groq"), "session-key")

        SESSION_PROVIDER_KEYS.clear()
        read.return_value = None
        self.assertEqual(
            provider_key("groq"), "environment-key"
        )

    @patch("providers.read_saved_credential")
    def test_status_does_not_expose_secret(self, read):
        read.return_value = {
            "key": "secret-value", "state": "verified"
        }

        status = credential_status("groq")

        self.assertEqual(
            status["credential_source"], "keychain"
        )
        self.assertTrue(status["configured"])
        self.assertNotIn("secret-value", str(status))

    @patch("providers.read_saved_credential")
    def test_locked_keychain_fails_closed(self, read):
        read.side_effect = keychain.KeychainError("Locked")

        self.assertEqual(provider_key("groq"), "")
        self.assertEqual(
            credential_status("groq")["credential_state"],
            "storage_unavailable",
        )

    @patch("provider_keychain._backend", return_value=None)
    def test_unsupported_platform(self, backend):
        self.assertIsNone(
            keychain.read_saved_credential("groq")
        )

        with self.assertRaises(keychain.KeychainError):
            keychain.save_saved_credential(
                "groq", "test-key", "verified"
            )

    @patch("app.save_saved_credential")
    @patch("app.validate_provider_key", return_value="verified")
    @patch("providers.read_saved_credential")
    def test_persistent_connection(self, read, validate, save):
        read.return_value = {
            "key": "saved-key", "state": "verified"
        }

        SESSION_PROVIDER_KEYS["groq"] = "old-session"

        status = connect_provider(
            "groq",
            self.request,
            {"key": "saved-key", "persist_key": True},
        )

        save.assert_called_once_with(
            "groq", "saved-key", "verified"
        )

        self.assertNotIn("groq", SESSION_PROVIDER_KEYS)
        self.assertEqual(
            status["credential_source"], "keychain"
        )

    @patch("app.save_saved_credential")
    @patch("app.validate_provider_key", return_value="verified")
    def test_failed_save_preserves_session(self, validate, save):
        SESSION_PROVIDER_KEYS["groq"] = "previous-key"
        save.side_effect = keychain.KeychainError("Unavailable")

        with self.assertRaises(HTTPException) as ctx:
            connect_provider(
                "groq",
                self.request,
                {"key": "new-key", "persist_key": True},
            )

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(
            SESSION_PROVIDER_KEYS["groq"], "previous-key"
        )

    @patch("app.save_saved_credential")
    @patch("app.validate_provider_key", return_value="verified")
    def test_default_remains_session_only(self, validate, save):
        connect_provider(
            "groq", self.request, {"key": "temporary-key"}
        )

        save.assert_not_called()
        self.assertEqual(
            provider_key("groq"), "temporary-key"
        )

    @patch("app.delete_saved_credential")
    @patch("providers.read_saved_credential", return_value=None)
    def test_remove_saved_key(self, read, delete):
        status = disconnect_provider("groq", self.request)

        delete.assert_called_once_with("groq")
        self.assertEqual(
            status["credential_source"], "environment"
        )

    @patch("app.delete_saved_credential")
    @patch("providers.read_saved_credential")
    def test_remove_session_preserves_saved_key(self, read, delete):
        SESSION_PROVIDER_KEYS["groq"] = "session-key"
        read.return_value = {
            "key": "saved-key", "state": "verified"
        }

        status = disconnect_provider("groq", self.request)

        delete.assert_not_called()
        self.assertEqual(
            status["credential_source"], "keychain"
        )

    @patch("provider_keychain._backend")
    def test_storage_errors_do_not_expose_secret(self, backend):
        backend.return_value.set_password.side_effect = (
            RuntimeError("secret-value")
        )

        with self.assertRaises(keychain.KeychainError) as ctx:
            keychain.save_saved_credential(
                "groq", "secret-value", "verified"
            )

        self.assertNotIn("secret-value", str(ctx.exception))
