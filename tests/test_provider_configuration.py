import json
import os
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import URL

import provider_configuration as config

from providers import (
    PROVIDERS,
    provider_enabled,
    get_provider_client,
    get_provider_model,
    get_available_models,
    SESSION_PROVIDER_KEYS,
)

from app import (
    export_provider_configuration,
    import_provider_configuration,
)


class ProviderConfigurationTests(unittest.TestCase):

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)

        self.path = Path(directory.name) / "providers.json"

        patcher = patch.object(
            config, "CONFIG_PATH", self.path
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = patch.dict(
            SESSION_PROVIDER_KEYS, {}, clear=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.request = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            url=URL("http://127.0.0.1:8000"),
            headers={"origin": "http://127.0.0.1:8000"},
        )

    def payload(self, mode="enabled", label="My Groq"):
        return {
            "version": 1,
            "providers": {
                "groq": {"mode": mode, "label": label}
            },
        }

    def save(self, mode="enabled", label="My Groq"):
        return config.save_configuration(
            self.payload(mode, label), PROVIDERS
        )

    def test_default_configuration(self):
        self.assertEqual(
            config.load_configuration(PROVIDERS),
            {"version": 1, "providers": {}},
        )
        self.assertTrue(provider_enabled("groq"))

    def test_configuration_persists(self):
        self.save("enabled", "My preferred provider")

        self.assertTrue(self.path.exists())
        self.assertEqual(
            config.load_configuration(PROVIDERS),
            self.payload("enabled", "My preferred provider"),
        )

    def test_configuration_file_permissions(self):
        self.save()

        self.assertEqual(
            os.stat(self.path).st_mode & 0o777, 0o600
        )

    @patch("providers.provider_key")
    def test_disabled_provider_blocks_client(self, key):
        self.save("disabled")

        with self.assertRaises(ValueError):
            get_provider_client("groq")

        key.assert_not_called()

    def test_disabled_provider_blocks_model(self):
        self.save("disabled")

        with self.assertRaises(ValueError):
            get_provider_model("groq")

    @patch("providers.provider_key")
    def test_disabled_provider_blocks_model_discovery(self, key):
        self.save("disabled")

        self.assertEqual(
            get_available_models("groq"), []
        )

        key.assert_not_called()

    def test_removed_provider_is_disabled(self):
        self.save("removed")

        self.assertFalse(provider_enabled("groq"))
        self.assertEqual(
            config.preference("groq", PROVIDERS)["label"],
            "",
        )

    def test_removed_provider_can_be_restored(self):
        self.save("removed")
        self.save("enabled")

        self.assertTrue(provider_enabled("groq"))

    def test_unknown_provider_rejected(self):
        payload = self.payload()
        payload["providers"]["unknown"] = {
            "mode": "enabled",
            "label": "",
        }

        with self.assertRaises(config.ConfigurationError):
            config.save_configuration(payload, PROVIDERS)

        self.assertFalse(self.path.exists())

    def test_invalid_modes_rejected(self):
        for mode in ("invalid", True, [], None):
            with self.subTest(mode=mode):
                with self.assertRaises(config.ConfigurationError):
                    self.save(mode)

    def test_invalid_labels_rejected(self):
        for label in ("x" * 81, "bad\nlabel", None):
            with self.subTest(label=label):
                with self.assertRaises(config.ConfigurationError):
                    self.save(label=label)

    def test_api_key_field_rejected(self):
        payload = self.payload()
        payload["providers"]["groq"]["api_key"] = "secret"

        with self.assertRaises(config.ConfigurationError):
            config.save_configuration(payload, PROVIDERS)

    def test_endpoint_override_rejected(self):
        payload = self.payload()
        payload["providers"]["groq"]["base_url"] = (
            "https://example.com"
        )

        with self.assertRaises(config.ConfigurationError):
            config.save_configuration(payload, PROVIDERS)

    def test_invalid_import_preserves_previous_configuration(self):
        self.save()
        previous = self.path.read_bytes()

        with self.assertRaises(config.ConfigurationError):
            config.save_configuration(
                {"version": 999, "providers": {}},
                PROVIDERS,
            )

        self.assertEqual(self.path.read_bytes(), previous)

    def test_corrupted_configuration_does_not_enable_provider(self):
        self.path.write_text("invalid JSON")

        with self.assertRaises(config.ConfigurationError):
            provider_enabled("groq")

    def test_configuration_size_limit(self):
        self.path.write_bytes(
            b"x" * (config.MAX_BYTES + 1)
        )

        with self.assertRaises(config.ConfigurationError):
            config.load_configuration(PROVIDERS)

    def test_export_does_not_include_stored_credentials(self):
        SESSION_PROVIDER_KEYS["groq"] = "test-secret-key"
        self.save()

        exported = json.dumps(
            export_provider_configuration()
        )

        self.assertNotIn("test-secret-key", exported)
        self.assertNotIn("api_key", exported)
        self.assertNotIn("base_url", exported)

    def test_import_preserves_credentials(self):
        SESSION_PROVIDER_KEYS["groq"] = "existing-key"

        import_provider_configuration(
            self.request, self.payload("removed")
        )

        self.assertEqual(
            SESSION_PROVIDER_KEYS["groq"], "existing-key"
        )

    def test_remote_import_rejected(self):
        self.request.client.host = "192.168.1.100"

        with self.assertRaises(HTTPException) as ctx:
            import_provider_configuration(
                self.request, self.payload()
            )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertFalse(self.path.exists())

    def test_cross_origin_import_rejected(self):
        self.request.headers["origin"] = "https://example.com"

        with self.assertRaises(HTTPException) as ctx:
            import_provider_configuration(
                self.request, self.payload()
            )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertFalse(self.path.exists())

    def test_import_export_roundtrip(self):
        payload = self.payload()

        import_provider_configuration(self.request, payload)

        self.assertEqual(
            export_provider_configuration(), payload
        )


if __name__ == "__main__":
    unittest.main()
