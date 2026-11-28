"""Bounded, explicitly authorized free-model continuation for the coding runner."""
import uuid

from coding_agent import PROVIDER, MODEL, run_issue
from coding_checkpoints import CheckpointStore
from terminal_runner import CodingError, CodingCancelled, committed_source

SUPPORTED = ("gemini", "groq", "openrouter")
MAX_STAGES = 3


def free_candidates(provider):
    """Only enabled, credentialed providers with published free tool-capable models."""
    from providers import discover_free_models, provider_enabled, provider_key
    if provider not in SUPPORTED or not provider_enabled(provider) or not provider_key(provider):
        return []
    models = discover_free_models(provider)
    return [
        {"provider": provider, "model": m["id"], "name": m.get("name", m["id"]),
         "context_length": m.get("context_length", 0)}
        for m in models if m.get("capabilities", {}).get("tools")
        and (m.get("free_tier") is True or m.get("free") is True)
        and (provider != "openrouter" or m["id"].endswith(":free"))
        # Require sufficient advertised context for the bounded stage.
        and (m.get("context_length") or 0) >= 65536
    ]


def catalog():
    models, unavailable = [], []
    for provider in SUPPORTED:
        try:
            models.extend(free_candidates(provider))
        except Exception:
            unavailable.append(provider)
    return {"models": models, "unavailable": unavailable}


def authorize(selection):
    """Revalidate exact provider/model immediately before each dispatch."""
    provider, model = selection["provider"], selection["model"]
    if not any(m["model"] == model for m in free_candidates(provider)):
        raise CodingError("Selected model is not currently verified as free, available and tool-capable.")


def model_interval(provider, model):
    # The free plan can vary by model; conservative fallback where limits are unknown.
    from storage import get_daily_usage
    from storage import get_gemini_limits
    for row in get_daily_usage():
        if row["provider"] == provider and row["model"] == model:
            if row.get("rpd_limit") and row.get("requests_remaining") == 0:
                raise CodingError("Local accounting indicates exhausted request quota.")
            if row.get("rpm_limit"):
                return max(5.5, 65 / row["rpm_limit"])
    if provider == "gemini":
        limits = get_gemini_limits(model)
        if limits and limits.get("rpm"):
            return max(5.5, 65 / limits["rpm"])
    return 13.0


def client_for(provider):
    from providers import get_provider_client
    # Prevent implicit SDK retries (all attempts belong to our bounded stage).
    return get_provider_client(provider).with_options(max_retries=0, timeout=45)


def run_chain(number, stop, emit, selections=None, checkpoint_id=None, *,
              store=None, source=committed_source, stage=run_issue,
              validate=authorize, interval_for=model_interval, client=client_for):
    """Each stage starts fresh model context, but restores the same verified source."""
    store = store if store is not None else CheckpointStore()
    selections = selections or [{"provider": PROVIDER, "model": MODEL}]
    if len(selections) > MAX_STAGES or len({(s["provider"], s["model"]) for s in selections}) != len(selections):
        raise CodingError("Select up to three distinct coding models.")
    if any(s["provider"] not in SUPPORTED for s in selections):
        raise CodingError("Unsupported coding provider.")
    revision, original = source()
    identifier = checkpoint_id or uuid.uuid4().hex
    current = original
    history = []
    if checkpoint_id:
        record = store.read(checkpoint_id)
        if record["issue"] != number or record["revision"] != revision:
            raise CodingError("Checkpoint issue or base commit does not match this checkout.")
        current = record["files"]
        history = record["history"]
    emit("checkpoint", {"id": identifier, "issue": number, "source_commit": revision})
    last = None
    for index, selection in enumerate(selections):
        if stop.is_set():
            raise CodingCancelled("Coding run cancelled.")
        provider, model = selection["provider"], selection["model"]
        try:
            # Recheck the current published free listing, even for the default.
            validate(selection)
            interval = interval_for(provider, model)
        except Exception as error:
            emit("status", {"message": f"Skipping {provider}/{model}: free eligibility or quota could not be confirmed.",
                            "type": type(error).__name__})
            continue
        emit("status", {"message": f"Stage {index + 1}/{len(selections)}: {provider}/{model}"})
        stage_history = list(history)

        def save_stage(rev, files, progress):
            nonlocal current, history
            if rev != revision:
                raise CodingError("Stage source commit changed.")
            store.save(identifier, number, revision, files, stage_history + [progress])
            current = files
            history = (stage_history + [progress])[-12:]
            emit("checkpoint", {"id": identifier, "changed_paths": progress.get("changed_paths", []),
                                "model": model, "reason": progress.get("reason")})

        result = stage(number, stop, emit, source=lambda: (revision, original),
                       initial=current if history else None, handoff=history[-3:] if history else None,
                       provider=provider, model=model, interval=interval,
                       client_factory=lambda: client(provider), checkpoint=save_stage)
        last = result
        if result["status"] == "review":
            break
        emit("status", {"message": "Stage incomplete; retaining checkpoint for the next authorized model."})
    if last is None:
        raise CodingError("No selected model passed free-eligibility and quota checks. Load the free-model catalog and select an available model.")
    return {**last, "checkpoint_id": identifier, "stages": history[-12:],
            "message": f"{last['status']} — {last.get('reason', '')}"}
