import os
import re


def model_display_name(model_id, official_name=None):
    """Return a readable model name without changing its API identifier."""
    overrides = {
        "openai/gpt-oss-120b": "GPT OSS 120B",
        "openai/gpt-oss-20b": "GPT OSS 20B",
        "nvidia/nemotron-3.5-super-vl:free": "Nemotron 3.5 Super VL (Free)",
        "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
        "gemma-4-26b-a4b-it": "Gemma 4 26B A4B IT",
    }
    if model_id in overrides:
        return overrides[model_id]
    if official_name and official_name != model_id:
        return official_name
    name = model_id.split("/")[-1].removesuffix(":free")
    name = re.sub(r"[-_]+", " ", name)
    name = re.sub(r"\b(\d+)b\b", r"\1B", name, flags=re.I)
    name = name.title()
    for old, new in (("Gpt", "GPT"), ("Oss", "OSS"), ("Vl", "VL"),
                     ("A4B", "A4B"), ("It", "IT")):
        name = re.sub(rf"\b{old}\b", new, name)
    return name
from pathlib import Path

import httpx
from dotenv import load_dotenv
from runtime import ENV_FILE
from openai import OpenAI

load_dotenv(ENV_FILE, override=True)

PROVIDERS = {
    "groq": {
        "name": "Groq",
        "api_key": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
        "display_model": "GPT OSS 120B",
        "recommended": [
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-20b",
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "api_key": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": os.getenv("OPENROUTER_MODEL", ""),
        "display_model": "Nemotron 3.5 Super VL (Free)",
        "recommended": [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3.5-super-vl:free",
            "nvidia/nemotron-3.5-lightning:free",
            "google/gemma-4-31b-it:free",
            "inclusionai/ling-3.2-flash:free",
        ],
    },
    "gemini": {
        "name": "Google Gemini",
        "api_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        "display_model": "Gemini 3.5 Flash Lite",
        "recommended": [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.1-pro-preview",
            "gemini-3.5-flash-lite",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        ],
    },
}


def get_provider_client(provider_name):
    if provider_name not in PROVIDERS:
        raise ValueError("Unsupported provider")

    config = PROVIDERS[provider_name]
    api_key = os.getenv(config["api_key"])

    if not api_key:
        raise ValueError(f"{config['name']} API key is not configured")

    if provider_name == "gemini":
        return OpenAI(
            api_key=api_key,
            base_url=config["base_url"],
            timeout=45.0,
            max_retries=0,
        )

    return OpenAI(api_key=api_key, base_url=config["base_url"])


def get_provider_model(provider_name):
    if provider_name not in PROVIDERS:
        raise ValueError("Unsupported provider")

    config = PROVIDERS[provider_name]
    model = config["default_model"]

    if not model:
        raise ValueError(f"No model configured for {config['name']}")

    return model


def get_available_models(provider_name):
    if provider_name not in PROVIDERS:
        raise ValueError("Unsupported provider")

    config = PROVIDERS[provider_name]
    api_key = os.getenv(config["api_key"])

    if not api_key:
        return []

    models = []

    try:
        if provider_name == "groq":
            client = get_provider_client(provider_name)
            available = client.models.list()

            for model in available.data:
                model_id = model.id
                if any(term in model_id.lower() for term in (
                    "whisper", "orpheus", "guard", "embedding"
                )):
                    continue
                model_lower = model_id.lower()
                groq_vision_models = {
                    "meta-llama/llama-4-scout-17b-16e-instruct",
                    "meta-llama/llama-4-maverick-17b-128e-instruct",
                    "meta-llama/llama-3.2-11b-vision-preview",
                    "meta-llama/llama-3.2-90b-vision-preview",
                }
                supports_image = model_lower in groq_vision_models
                models.append({
                    "id": model_id,
                    "name": model_display_name(model_id),
                    "capabilities": {
                        "text": True,
                        "image": supports_image,
                        "document": False,
                    },
                })

        elif provider_name == "openrouter":
            response = httpx.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            response.raise_for_status()

            for model in response.json().get("data", []):
                model_id = model.get("id", "")
                architecture = model.get("architecture", {})
                modality = architecture.get("modality", "")

                if not model_id or modality not in ("text->text", "text+image->text"):
                    continue

                pricing = model.get("pricing", {})
                if pricing.get("prompt") != "0" or pricing.get("completion") != "0":
                    continue

                input_modalities = architecture.get("input_modalities", [])
                models.append({
                    "id": model_id,
                    "name": model_display_name(model_id, model.get("name")),
                    "capabilities": {
                        "text": "text" in input_modalities,
                        "image": "image" in input_modalities,
                        "document": "file" in input_modalities or "pdf" in input_modalities,
                    },
                    "free": (
                        model.get("pricing", {}).get("prompt") == "0"
                        and model.get("pricing", {}).get("completion") == "0"
                    ),
                })

        elif provider_name == "gemini":
            response = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
                timeout=30,
            )
            response.raise_for_status()

            for model in response.json().get("models", []):
                model_id = model.get("name", "").removeprefix("models/")
                methods = model.get("supportedGenerationMethods", [])

                if (
                    "generateContent" not in methods
                    or not model_id
                    or any(term in model_id.lower() for term in (
                        "tts", "image", "transcribe", "lyria",
                        "robotics", "deep-research", "computer-use",
                        "customtools", "antigravity",
                    ))
                ):
                    continue

                input_methods = model.get("supportedGenerationMethods", [])
                # Gemini ListModels does not reliably expose input modalities.
                # Use a conservative known-model fallback.
                model_lower = model_id.lower()
                supports_image = (
                    model_lower.startswith(("gemini-", "gemma-"))
                    and not any(term in model_lower for term in ("embedding", "tts"))
                )
                supports_document = model_lower.startswith("gemini-") and supports_image
                models.append({
                    "id": model_id,
                    "name": model_display_name(model_id, model.get("displayName")),
                    "capabilities": {
                        "text": "generateContent" in input_methods,
                        "image": supports_image,
                        "document": supports_document,
                    },
                })

    except Exception as error:
        print(f"Model discovery failed for {provider_name}: {error}")
        return []

    by_id = {model["id"]: model for model in models}
    recommended = config["recommended"]

    ordered = []
    for model_id in recommended:
        if model_id in by_id:
            ordered.append(by_id.pop(model_id))

    remaining = sorted(
        by_id.values(),
        key=lambda model: (
            not model.get("free", False),
            model["name"].lower(),
        ),
    )

    return ordered + remaining
