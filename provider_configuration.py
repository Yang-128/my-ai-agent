"""Secret-free, validated provider configuration storage."""

import json
import os
import tempfile
from threading import RLock

from runtime import DATA_DIR


CONFIG_PATH = DATA_DIR / "provider_configuration.json"
MAX_BYTES = 32768
LOCK = RLock()
MODES = {"auto", "enabled", "disabled", "removed"}


class ConfigurationError(ValueError):
    pass


def default_preference():
    return {"mode": "auto", "label": ""}


def validate_configuration(payload, known_providers):
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "providers"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["providers"], dict)
    ):
        raise ConfigurationError("Invalid configuration format.")

    result = {}

    for provider, settings in payload["providers"].items():
        if provider not in known_providers:
            raise ConfigurationError("Unknown provider.")

        if (
            not isinstance(settings, dict)
            or set(settings) != {"mode", "label"}
        ):
            raise ConfigurationError(
                "Only mode and label may be imported."
            )

        mode = settings["mode"]
        label = settings["label"]

        if not isinstance(mode, str) or mode not in MODES:
            raise ConfigurationError("Invalid provider mode.")

        if (
            not isinstance(label, str)
            or len(label) > 80
            or (label and not label.isprintable())
        ):
            raise ConfigurationError("Invalid provider label.")

        result[provider] = {
            "mode": mode,
            "label": "" if mode == "removed" else label.strip(),
        }

    return {"version": 1, "providers": result}


def load_configuration(known_providers):
    with LOCK:
        try:
            with CONFIG_PATH.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return {"version": 1, "providers": {}}
        except OSError:
            raise ConfigurationError(
                "Could not read provider configuration."
            ) from None

        if len(raw) > MAX_BYTES:
            raise ConfigurationError("Configuration is too large.")

        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ConfigurationError(
                "Saved provider configuration is invalid."
            ) from None

        return validate_configuration(payload, known_providers)


def save_configuration(payload, known_providers):
    clean = validate_configuration(payload, known_providers)
    raw = json.dumps(clean, indent=2, ensure_ascii=False).encode()

    if len(raw) > MAX_BYTES:
        raise ConfigurationError("Configuration is too large.")

    with LOCK:
        temp = None

        try:
            with tempfile.NamedTemporaryFile(
                dir=CONFIG_PATH.parent,
                prefix=".provider-config-",
                delete=False,
            ) as stream:
                temp = stream.name
                os.chmod(temp, 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())

            os.replace(temp, CONFIG_PATH)
        except OSError:
            raise ConfigurationError(
                "Could not save provider configuration."
            ) from None
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    return clean


def preference(provider, known_providers):
    return load_configuration(
        known_providers
    )["providers"].get(provider, default_preference())


def is_enabled(provider, known_providers):
    return preference(
        provider, known_providers
    )["mode"] in {"auto", "enabled"}
