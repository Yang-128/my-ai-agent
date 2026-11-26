"""Compare one free model and its own quota from each provider."""
import math
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from lxml import html

from providers import FREE_TIER_PAGES, PROVIDERS, free_plan_ids, public_catalog
from storage import get_gemini_limits

ROUTER_LIMITS = "https://openrouter.ai/docs/api/reference/limits"
GEMINI_LIMITS = "https://ai.google.dev/gemini-api/docs/rate-limits"
KILO_LIMITS = "https://kilo.ai/docs/gateway/usage-and-billing"


def quota_number(text):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KM]?)", text.strip().upper().replace(",", ""))
    if match:
        return int(float(match[1]) * {"": 1, "K": 1000, "M": 1000000}[match[2]])


def groq_limits(page):
    root = html.fromstring(page)
    table = root.xpath("//table[contains(., 'Free Plan Limits')]")
    if not table:
        return {}
    columns = [c.text_content().strip() for c in table[0].xpath(".//tr[last()]/th")]
    limits = {}
    for row in table[0].xpath("following::table[1]//tr"):
        values = dict(zip(columns, [c.text_content().strip() for c in row.xpath("./td")]))
        if values.get("MODEL ID"):
            limits[values["MODEL ID"]] = (quota_number(values.get("RPD", "")), quota_number(values.get("TPD", "")))
    return limits


def router_limit(page):
    for line in page.splitlines():
        match = re.fullmatch(r"\|\s*Less than [^|]+\|\s*[\d,]+\s*\|\s*([\d,]+)\s*\|", line)
        if match:
            return quota_number(match[1])


def model_fit(model):
    inputs = (model.get("modalities") or {}).get("input", [])
    context = (model.get("limit") or {}).get("context") or 0
    uses = []
    points = 0
    for flag, weight, description in [
        (model.get("tool_call"), 40, "using agent tools"),
        (model.get("reasoning"), 30, "multi-step reasoning"),
        ("image" in inputs, 20, "working with images"),
        (context >= 128000, 10, "reading long documents"),
    ]:
        if flag:
            uses.append(description)
            points += weight
    return points, ", ".join(uses) or "text chat and writing"


def evaluate(model, rpd, tpd):
    quality, uses = model_fit(model)
    bounds = [n for n in (rpd, tpd // 2000 if tpd is not None else None) if n is not None]
    daily = min(bounds) if bounds else None
    allowance = 100 * min(1, math.log1p(daily) / math.log1p(1000)) if daily is not None else 0
    score = 0.6 * quality + 0.4 * allowance
    return (daily is not None, score, quality), uses


def optional_page(url):
    import httpx
    try:
        return public_catalog(url).text
    except (httpx.HTTPError, ValueError):
        return ""


def ranked_providers():
    with ThreadPoolExecutor(max_workers=4) as pool:
        directory = pool.submit(public_catalog, "https://models.dev/api.json")
        groq = pool.submit(optional_page, FREE_TIER_PAGES["groq"])
        gemini = pool.submit(optional_page, FREE_TIER_PAGES["gemini"])
        router = pool.submit(optional_page, ROUTER_LIMITS + ".md")
        catalog = directory.result().json()
        groq_page, gemini_page = groq.result(), gemini.result()
        router_rpd = router_limit(router.result())

    plan_ids = {}
    for key, page in [("groq", groq_page), ("gemini", gemini_page)]:
        try:
            plan_ids[key] = set(free_plan_ids(key, page)) if page else set()
        except ValueError:
            plan_ids[key] = set()
    groq_caps = groq_limits(groq_page) if plan_ids["groq"] else {}
    offers = []
    for catalog_id, provider in catalog.items():
        key = "gemini" if catalog_id == "google" else catalog_id
        url = FREE_TIER_PAGES.get(key) or provider.get("doc") or ""
        if urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
            continue
        options = []
        for model in provider.get("models", {}).values():
            model_id = model.get("id", "")
            cost = model.get("cost") or {}
            modalities = model.get("modalities") or {}
            if ("text" not in modalities.get("input", []) or "text" not in modalities.get("output", [])
                    or model.get("status") == "deprecated"):
                continue
            if key in plan_ids:
                if model_id not in plan_ids[key]:
                    continue
            elif not (re.search(r"(?:^|[-:/ ])free(?:$|[-:/ )])", model_id.lower())
                      and cost.get("input") == cost.get("output") == 0):
                continue

            rpd = tpd = None
            source = url
            note = "Daily allowance unverified; check the provider’s current terms."
            if key == "groq":
                rpd, tpd = groq_caps.get(model_id, (None, None))
                note = "Free plan, per model."
            elif key == "gemini":
                rpd = (get_gemini_limits(model_id) or {}).get("rpd")
                source = GEMINI_LIMITS
                note = "App reference for this model; confirm your project’s limit in AI Studio." if rpd is not None else "Daily allowance varies by model and project; check AI Studio."
            elif key == "openrouter" and model_id.endswith(":free"):
                rpd = router_rpd
                source = ROUTER_LIMITS
                note = "Shared across free variants on a no-purchase account."
            elif key == "kilo":
                source = KILO_LIMITS
                note = "200 requests/hour per IP across free models. Daily cap unverified."
            rank, uses = evaluate(model, rpd, tpd)
            limits = []
            if rpd is not None:
                limits.append(f"{rpd:,} requests/day")
            if tpd is not None:
                limits.append(f"{tpd:,} tokens/day")
            if not limits and key in ("groq", "openrouter"):
                limits.append("Daily cap unverified")
            options.append((rank, model_id, {
                "example": model.get("name") or model_id, "strengths": uses,
                "limits": " · ".join(limits + [note]), "limit_url": source,
            }))
        if not options and key not in PROVIDERS:
            continue
        if options:
            rank, _, details = max(options, key=lambda item: (item[0], item[1]))
        else:
            rank = (False, -1, 0)
            details = {"example": "", "strengths": "Free model details unavailable.",
                       "limits": "Daily cap unverified.", "limit_url": url}
        offers.append((rank, {"id": key, "name": PROVIDERS.get(key, provider)["name"], "supported": key in PROVIDERS,
                              "url": url, "comparable": rank[0], **details}))
    offers.sort(key=lambda item: (-item[0][0], -item[0][1], item[1]["name"].casefold()))
    return [offer for _, offer in offers]
