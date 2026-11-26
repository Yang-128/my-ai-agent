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


EXTERNAL_CONNECTIONS = {
    "kilo": ("Kilo Gateway", "KILO_API_KEY", "https://ai-gateway.kilo.ai/api/v1",
             "https://kilo.ai/docs/gateway/authentication"),
    "opencode": ("OpenCode", "OPENCODE_API_KEY", "https://opencode.ai/inference/openai/v1",
                 "https://opencode.ai/docs/console/api/inference/"),
}
for provider_id, (name, key, endpoint, guide) in EXTERNAL_CONNECTIONS.items():
    PROVIDERS[provider_id] = {
        "name": name, "api_key": key, "base_url": endpoint,
        "setup_url": guide, "default_model": "", "recommended": [],
    }


def is_listed_free_chat(model):
    cost = model.get("cost") or {}
    modalities = model.get("modalities") or {}
    return (bool(re.search(r"(?:^|[-:/ ])free(?:$|[-:/ )])", model.get("id", "").lower()))
            and cost.get("input") == cost.get("output") == 0
            and "text" in modalities.get("input", []) and "text" in modalities.get("output", [])
            and model.get("status") != "deprecated")


def external_free_models(provider):
    directory = public_catalog("https://models.dev/api.json").json()
    models = directory.get(provider, {}).get("models", {}).values()
    return [{
        "id": model["id"], "name": model.get("name") or model["id"],
        "context_length": (model.get("limit") or {}).get("context") or 0,
        "capabilities": {"text": True, "tools": bool(model.get("tool_call")),
                         "image": "image" in model["modalities"]["input"]},
        "free": True,
    } for model in models if is_listed_free_chat(model)]


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

    if provider_name in EXTERNAL_CONNECTIONS:
        return OpenAI(api_key=api_key, base_url=config["base_url"],
                      http_client=httpx.Client(follow_redirects=False), timeout=45.0)
    return OpenAI(api_key=api_key, base_url=config["base_url"])


def get_provider_model(provider_name):
    if provider_name not in PROVIDERS:
        raise ValueError("Unsupported provider")

    config = PROVIDERS[provider_name]
    model = config["default_model"]

    if not model:
        raise ValueError(f"No model configured for {config['name']}")

    return model


def free_chat_models(catalog):
    """Read public chat capabilities and explicitly zero-priced models."""
    models = []
    for model in catalog:
        architecture = model.get("architecture") or {}
        inputs = architecture.get("input_modalities") or []
        outputs = architecture.get("output_modalities") or []
        pricing = model.get("pricing") or {}
        if (not model.get("id") or "text" not in inputs or outputs != ["text"]
                or not {"prompt", "completion"} <= pricing.keys()
                or any(str(price) not in {"0", "0.0", "0.00"} for price in pricing.values())):
            continue
        models.append({
            "id": model["id"],
            "name": model_display_name(model["id"], model.get("name")),
            "context_length": model.get("context_length") or 0,
            "created": model.get("created") or 0,
            "capabilities": {
                "text": True, "image": "image" in inputs,
                "document": "file" in inputs or "pdf" in inputs,
                "tools": "tools" in (model.get("supported_parameters") or []),
            },
            "free": True,
        })
    return models


FREE_TIER_PAGES = {
    "groq": "https://console.groq.com/docs/rate-limits",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "openrouter": "https://openrouter.ai/models?max_price=0",
}


def public_catalog(url):
    response = httpx.get(url, follow_redirects=True, timeout=15)
    response.raise_for_status()
    return response


def free_plan_ids(provider, page):
    from lxml import html

    if provider == "groq":
        root = html.fromstring(page)
        rows = root.xpath("//table[contains(., 'Free Plan Limits')]/following::table[1]//tr/td[1]")
        ids = [cell.text_content().strip() for cell in rows]
    else:
        ids = []
        for section in re.split(r"(?m)^## ", page)[1:]:
            standard = section.split('{% /tab %}', 1)[0]
            model = re.search(r"\[`([a-z0-9.-]+)`\]", standard)
            if (model and "Free Tier" in standard
                    and all(re.search(r"(?im)^ *\* " + item + r" price[^\n]*\n *\* Free of charge", standard)
                            for item in ("Input", "Output"))):
                ids.append(model[1])
    if not ids:
        raise ValueError("Free plan listing unavailable")
    return [i for i in dict.fromkeys(ids) if not any(term in i.lower() for term in
            ("guard", "whisper", "orpheus", "transcribe", "image", "tts", "live", "embedding", "robotics"))]


def rank_models(models, provider):
    """Prefer general-purpose agent capabilities, then provider preferences and usable context."""
    preferred = PROVIDERS[provider]["recommended"]
    def order(model):
        caps = model["capabilities"]
        return (not caps.get("tools"), not caps.get("image"),
                preferred.index(model["id"]) if model["id"] in preferred else len(preferred),
                -min(model.get("context_length") or 0, 131072),
                -(model.get("created") or 0), model["id"])
    return sorted(models, key=order)


def discover_free_models(provider="openrouter"):
    if provider in EXTERNAL_CONNECTIONS:
        models = external_free_models(provider)
    elif provider == "openrouter":
        models = free_chat_models(public_catalog("https://openrouter.ai/api/v1/models").json()["data"])
    else:
        ids = free_plan_ids(provider, public_catalog(FREE_TIER_PAGES[provider]).text)
        available = {m["id"]: m for m in get_available_models(provider, strict=True)} if os.getenv(PROVIDERS[provider]["api_key"]) else None
        models = []
        for model_id in ids:
            if available is not None and model_id not in available:
                continue
            model = available[model_id].copy() if available is not None else {
                "id": model_id, "name": model_display_name(model_id), "capabilities": {"text": True}}
            model["free_tier"] = True
            models.append(model)
    return rank_models(models, provider)


def get_available_models(provider_name, *, strict=False):
    if provider_name not in PROVIDERS:
        raise ValueError("Unsupported provider")

    config = PROVIDERS[provider_name]
    api_key = os.getenv(config["api_key"])

    if not api_key:
        return []

    models = []

    try:
        if provider_name in EXTERNAL_CONNECTIONS:
            return rank_models(external_free_models(provider_name), provider_name)
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
                    "context_length": getattr(model, "context_window", 0),
                    "capabilities": {
                        "text": True,
                        "image": supports_image,
                        "tools": model_id in config["recommended"] or supports_image,
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

            models.extend(free_chat_models(response.json()["data"]))

        elif provider_name == "gemini":
            listed = []
            params = {"pageSize": 100}
            while True:
                response = httpx.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": api_key}, params=params, timeout=15,
                )
                response.raise_for_status()
                page = response.json()
                listed.extend(page.get("models", []))
                token = page.get("nextPageToken")
                if not token:
                    break
                params["pageToken"] = token

            for model in listed:
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
                    "context_length": model.get("inputTokenLimit", 0),
                    "capabilities": {
                        "text": "generateContent" in input_methods,
                        "image": supports_image,
                        "document": supports_document,
                        "tools": model_id in config["recommended"] and model_id.startswith("gemini-"),
                    },
                })

    except Exception as error:
        if strict:
            raise
        print(f"Model discovery failed for {provider_name}: {type(error).__name__}")
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
