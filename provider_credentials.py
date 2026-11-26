"""Credential validation without model inference or secret logging."""

import httpx


VALIDATION_ENDPOINTS = {
    "groq": (
        "https://api.groq.com/openai/v1/models",
        "Authorization",
        "data",
    ),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/models",
        "x-goog-api-key",
        "models",
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1/key",
        "Authorization",
        "data",
    ),
    "vercel": (
        "https://ai-gateway.vercel.sh/v1/credits",
        "Authorization",
        "balance",
    ),
}


class CredentialValidationError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def validate_provider_key(provider, key, *, allow_unverified=False):
    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= 4096
        or not key.isascii()
        or not key.isprintable()
        or any(c.isspace() for c in key)
    ):
        raise CredentialValidationError(
            422, "Enter a valid API key."
        )

    endpoint = VALIDATION_ENDPOINTS.get(provider)

    if endpoint is None:
        if allow_unverified is True:
            return "unverified"

        raise CredentialValidationError(
            409,
            "Automatic authentication is not supported for this "
            "provider. You may explicitly save an unverified session "
            "key, or verify it in the provider dashboard.",
        )

    url, header, expected_field = endpoint
    value = key if header == "x-goog-api-key" else "Bearer " + key

    try:
        response = httpx.get(
            url,
            headers={
                header: value,
                "Accept": "application/json",
            },
            timeout=12,
            follow_redirects=False,
            trust_env=False,
        )
    except httpx.RequestError:
        raise CredentialValidationError(
            503,
            "Could not contact the provider. "
            "The previous credential has been preserved.",
        ) from None

    if response.status_code in (400, 401, 403):
        raise CredentialValidationError(
            422,
            "The provider rejected authentication or access. "
            "Check your API key and its permissions.",
        )

    if response.status_code == 429:
        raise CredentialValidationError(
            503,
            "The provider is rate limiting validation. Try again later.",
        )

    if response.status_code == 402:
        raise CredentialValidationError(
            422,
            "The provider requires account credits or an eligible plan. "
            "No inference request was sent.",
        )

    if response.status_code != 200:
        raise CredentialValidationError(
            502,
            "The provider could not validate the key. "
            "Your previous credential has been preserved.",
        )

    try:
        payload = response.json()
    except ValueError:
        raise CredentialValidationError(
            502, "Unexpected provider response."
        ) from None

    valid = isinstance(payload, dict) and "error" not in payload

    if valid:
        if provider in {"groq", "gemini"}:
            valid = isinstance(payload.get(expected_field), list)
        elif provider == "openrouter":
            valid = isinstance(payload.get("data"), dict)
        elif provider == "vercel":
            valid = isinstance(
                payload.get("balance"), (str, int, float)
            )

    if not valid:
        raise CredentialValidationError(
            502,
            "The provider returned an unexpected validation response.",
        )

    return "verified"
