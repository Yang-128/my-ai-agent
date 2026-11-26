import os
import unittest

from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from starlette.datastructures import URL

from provider_credentials import (
    validate_provider_key,
    CredentialValidationError,
    VALIDATION_ENDPOINTS,
)

from providers import (
    SESSION_PROVIDER_KEYS,
    SESSION_PROVIDER_VALIDATION,
    credential_status,
    provider_key,
)

from app import connect_provider, disconnect_provider


class CredentialTests(unittest.TestCase):

    def setUp(self):
        for target in (
            "providers.read_saved_credential",
            "app.delete_saved_credential",
        ):
            patcher = patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)

        for mapping in (
            SESSION_PROVIDER_KEYS,
            SESSION_PROVIDER_VALIDATION,
        ):
            patcher = patch.dict(mapping, {}, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)

        patcher = patch.dict(os.environ, {
            "GROQ_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "GEMINI_API_KEY": "",
        })
        patcher.start()
        self.addCleanup(patcher.stop)

        self.request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            url=URL("http://127.0.0.1:8000"),
            headers={"origin": "http://127.0.0.1:8000"},
        )

    @patch("provider_credentials.httpx.get")
    def test_supported_providers(self, get):
        payloads = {
            "groq": {"data": []},
            "gemini": {"models": []},
            "openrouter": {"data": {"label": "test"}},
            "vercel": {"balance": "5.00"},
        }

        for provider, payload in payloads.items():
            with self.subTest(provider=provider):
                get.return_value = httpx.Response(
                    200, json=payload
                )
                self.assertEqual(
                    validate_provider_key(provider, "test-key"),
                    "verified",
                )
                url, header, _ = VALIDATION_ENDPOINTS[provider]
                args, kwargs = get.call_args

                self.assertEqual(args[0], url)
                self.assertEqual(
                    kwargs["headers"][header],
                    "test-key" if header == "x-goog-api-key"
                    else "Bearer test-key",
                )
                self.assertFalse(kwargs["follow_redirects"])
                self.assertFalse(kwargs["trust_env"])

    @patch("provider_credentials.httpx.get")
    def test_invalid_key_rejected(self, get):
        get.return_value = httpx.Response(
            401, json={"error": "test-secret"}
        )

        with self.assertRaises(CredentialValidationError) as ctx:
            validate_provider_key("groq", "test-secret")

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertNotIn("test-secret", str(ctx.exception))

    @patch("provider_credentials.httpx.get")
    def test_rate_limit_does_not_mean_invalid_key(self, get):
        get.return_value = httpx.Response(429)

        with self.assertRaises(CredentialValidationError) as ctx:
            validate_provider_key("groq", "test-key")

        self.assertEqual(ctx.exception.status_code, 503)

    @patch("provider_credentials.httpx.get")
    def test_network_failure(self, get):
        get.side_effect = httpx.ConnectError("test-secret")

        with self.assertRaises(CredentialValidationError) as ctx:
            validate_provider_key("groq", "test-secret")

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertNotIn("test-secret", str(ctx.exception))

    @patch("provider_credentials.httpx.get")
    def test_redirect_rejected(self, get):
        get.return_value = httpx.Response(
            302, headers={"Location": "https://example.com"}
        )

        with self.assertRaises(CredentialValidationError):
            validate_provider_key("groq", "test-key")

    @patch("provider_credentials.httpx.get")
    def test_malformed_success_rejected(self, get):
        for payload in ({}, [], {"error": "failure"}):
            get.return_value = httpx.Response(
                200, json=payload
            )
            with self.assertRaises(CredentialValidationError):
                validate_provider_key("groq", "test-key")

    @patch("provider_credentials.httpx.get")
    def test_invalid_format_never_contacts_provider(self, get):
        for key in ("", "contains spaces", "abc\n", None):
            with self.assertRaises(CredentialValidationError):
                validate_provider_key("groq", key)
        get.assert_not_called()

    @patch("provider_credentials.httpx.get")
    def test_unverified_requires_explicit_consent(self, get):
        for provider in (
            "kilo", "opencode", "zenmux", "tokenrouter"
        ):
            with self.assertRaises(CredentialValidationError):
                validate_provider_key(provider, "test-key")

            self.assertEqual(
                validate_provider_key(
                    provider,
                    "test-key",
                    allow_unverified=True,
                ),
                "unverified",
            )

        get.assert_not_called()

    @patch("app.validate_provider_key", return_value="verified")
    def test_valid_connection(self, validate):
        status = connect_provider(
            "groq", self.request, {"key": "test-secret"}
        )
        self.assertEqual(provider_key("groq"), "test-secret")
        self.assertEqual(status["credential_state"], "verified")
        self.assertTrue(status["configured"])
        self.assertNotIn("test-secret", str(status))

    @patch("app.validate_provider_key")
    def test_failed_replacement_preserves_previous_key(self, validate):
        SESSION_PROVIDER_KEYS["groq"] = "previous-key"
        SESSION_PROVIDER_VALIDATION["groq"] = "verified"

        validate.side_effect = CredentialValidationError(
            422, "Rejected"
        )

        with self.assertRaises(HTTPException):
            connect_provider(
                "groq", self.request, {"key": "bad-key"}
            )

        self.assertEqual(provider_key("groq"), "previous-key")
        self.assertEqual(
            credential_status("groq")["credential_state"],
            "verified",
        )

    @patch("app.validate_provider_key", return_value="verified")
    def test_successful_replacement(self, validate):
        SESSION_PROVIDER_KEYS["groq"] = "previous-key"

        connect_provider(
            "groq", self.request, {"key": "replacement-key"}
        )

        self.assertEqual(provider_key("groq"), "replacement-key")

    def test_remove_session_key(self):
        SESSION_PROVIDER_KEYS["groq"] = "test-key"
        SESSION_PROVIDER_VALIDATION["groq"] = "verified"

        status = disconnect_provider("groq", self.request)

        self.assertFalse(status["configured"])
        self.assertNotIn("groq", SESSION_PROVIDER_KEYS)
        self.assertNotIn("groq", SESSION_PROVIDER_VALIDATION)

    def test_remove_session_key_restores_environment(self):
        SESSION_PROVIDER_KEYS["groq"] = "session-key"

        with patch.dict(os.environ, {
            "GROQ_API_KEY": "environment-key"
        }):
            status = disconnect_provider("groq", self.request)

            self.assertEqual(
                provider_key("groq"), "environment-key"
            )
            self.assertEqual(
                status["credential_source"], "environment"
            )
            self.assertEqual(
                status["credential_state"], "unverified"
            )

    @patch("app.validate_provider_key")
    def test_cross_origin_requests_rejected(self, validate):
        self.request.headers["origin"] = "https://example.com"

        with self.assertRaises(HTTPException) as ctx:
            connect_provider(
                "groq", self.request, {"key": "test-key"}
            )

        self.assertEqual(ctx.exception.status_code, 403)
        validate.assert_not_called()

    def test_remote_removal_rejected(self):
        self.request.client.host = "192.168.1.100"
        SESSION_PROVIDER_KEYS["groq"] = "existing-key"

        with self.assertRaises(HTTPException) as ctx:
            disconnect_provider("groq", self.request)

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(provider_key("groq"), "existing-key")

    def test_unknown_provider_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            connect_provider(
                "unknown", self.request, {"key": "test-key"}
            )

        self.assertEqual(ctx.exception.status_code, 404)

    def test_missing_origin_rejected(self):
        self.request.headers = {}

        with self.assertRaises(HTTPException) as ctx:
            disconnect_provider("groq", self.request)

        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
