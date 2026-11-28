"use strict";
const byId = id => document.getElementById(id);
let runId = null, patch = "", pollTimer = null, sourceCommit = "";
let activeToken = "";
let catalogModels = [];
let checkpoints = [];
async function api(path, method = "GET", body) {
  const headers = {"X-Coding-Token": activeToken};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {method, headers, cache: "no-store",
    ...(body !== undefined ? {body: JSON.stringify(body)} : {})});
  if (!response.ok) {
    let error = {};
    try { error = await response.json(); } catch (_) {}
    throw new Error(typeof error.detail === "string" ? error.detail : "Request failed: " + response.status);
  }
  return response.json();
}
function busy(value) {
  byId("start").disabled = value;
  byId("stop").disabled = !value;
  byId("token").disabled = value;
  byId("issue").disabled = value;
  byId("approved").disabled = value;
  for (const id of ["discover", "checkpoint", "model-1", "model-2", "model-3"]) byId(id).disabled = value;
  byId("delete-checkpoint").disabled = value || !byId("checkpoint").value;
}
function selectedModels() {
  return ["model-1", "model-2", "model-3"].map(id => byId(id).value)
    .filter(Boolean).map(value => catalogModels[Number(value)]);
}
function fillCheckpoints(items, selected = "") {
  checkpoints = items;
  const select = byId("checkpoint");
  select.replaceChildren(new Option("New coding session", ""));
  for (const item of items) select.add(new Option(
    "Issue #" + item.issue + " — " + (item.last_model || "saved") + " — " + item.id.slice(0, 8), item.id));
  select.value = selected;
  byId("delete-checkpoint").disabled = !select.value;
}
async function refreshCheckpoints(selected = "") {
  const result = await api("/api/coding/checkpoints");
  fillCheckpoints(result.checkpoints || [], selected);
}
byId("checkpoint").addEventListener("change", () => {
  const record = checkpoints.find(c => c.id === byId("checkpoint").value);
  if (record) byId("issue").value = record.issue;
  byId("delete-checkpoint").disabled = !record;
  byId("approved").checked = false;
});
byId("discover").addEventListener("click", async () => {
  activeToken = byId("token").value.trim();
  byId("discover").disabled = true;
  try {
    await refreshCheckpoints();
    const result = await api("/api/coding/catalog");
    catalogModels = result.models || [];
    for (const id of ["model-1", "model-2", "model-3"]) {
      const select = byId(id);
      select.replaceChildren(new Option(id === "model-1" ? "Default (Gemini Flash-Lite)" : "None", ""));
      catalogModels.forEach((m, i) => select.add(new Option(m.provider + " / " + m.name + " (" + m.model + ")", String(i))));
    }
    byId("models").hidden = false;
    byId("approved").checked = false;
    byId("status").textContent = "Loaded " + catalogModels.length + " free-listed coding models. " +
      ((result.unavailable || []).length ? "Discovery unavailable: " + result.unavailable.join(", ") + "." : "");
  } catch (error) { byId("status").textContent = error.message; }
  finally { byId("discover").disabled = false; }
});
byId("delete-checkpoint").addEventListener("click", async () => {
  const id = byId("checkpoint").value;
  if (!id || !confirm("Delete this saved coding checkpoint?")) return;
  try {
    await api("/api/coding/checkpoints/" + encodeURIComponent(id), "DELETE");
    await refreshCheckpoints();
  } catch (error) { byId("status").textContent = error.message; }
});
for (const id of ["model-1", "model-2", "model-3"]) byId(id).addEventListener("change", () => {
  byId("approved").checked = false;
});
async function poll() {
  try {
    const run = await api("/api/coding/" + encodeURIComponent(runId));
    byId("activity").textContent = run.events.map(e =>
      e.kind.toUpperCase() + " " + JSON.stringify(e.data)).join("\n\n");
    byId("activity").scrollTop = byId("activity").scrollHeight;
    if (run.status === "running") {
      byId("status").textContent = "Working on issue #" + run.issue + "…";
      pollTimer = setTimeout(poll, 1500);
      return;
    }
    busy(false);
    const result = run.result || {};
    byId("approved").checked = false;
    byId("status").textContent = result.message || ("Run ended: " + run.status + (result.reason ? " — " + result.reason : ""));
    if (result.checkpoint_id) {
      try { await refreshCheckpoints(result.checkpoint_id); } catch (_) { /* Saved checkpoint remains on disk. */ }
    }
    patch = result.patch || "";
    sourceCommit = result.source_commit || "";
    byId("review").hidden = !patch;
    if (patch) {
      byId("result").textContent = (run.status === "review" ? "Standard checks returned exit code 0. Review before applying." :
        run.status === "partial_review" ? "Partial patch: standard checks passed, but the issue is not confirmed complete." :
        "Standard checks did not pass; this is an unverified proposed patch.") + "\n" + (result.summary || "");
      byId("commit").textContent = "Source commit: " + sourceCommit;
      byId("patch").textContent = patch;
    }
  } catch (error) {
    byId("status").textContent = "Polling interrupted: " + error.message + ". Retrying; the run may still be active.";
    pollTimer = setTimeout(poll, 4000);
  }
}
byId("start").addEventListener("click", async () => {
  const issue = Number(byId("issue").value);
  if (!Number.isInteger(issue) || issue < 1 || issue > 1000000 || !byId("approved").checked) {
    byId("status").textContent = "Enter an issue number and approve the workspace and data sharing.";
    return;
  }
  activeToken = byId("token").value.trim();
  clearTimeout(pollTimer);
  byId("review").hidden = true;
  byId("activity").textContent = "";
  busy(true);
  try {
    let models = selectedModels();
    if (!byId("model-1").value && models.length) {
      const primary = catalogModels.find(m => m.provider === "gemini" && m.model === "gemini-3.5-flash-lite");
      if (!primary) throw new Error("Select an explicit first model before adding fallbacks.");
      models = [primary, ...models];
    }
    const keys = models.map(m => m.provider + "/" + m.model);
    if (new Set(keys).size !== keys.length) throw new Error("Choose distinct models.");
    const run = await api("/api/coding/start", "POST", {
      issue, approved: true, models: models.map(({provider, model}) => ({provider, model})),
      checkpoint_id: byId("checkpoint").value || null
    });
    runId = run.run_id;
    poll();
  } catch (error) {
    byId("status").textContent = error.message;
    busy(false);
  }
});
byId("stop").addEventListener("click", async () => {
  if (!runId) return;
  try {
    await api("/api/coding/" + encodeURIComponent(runId) + "/stop", "POST", {});
    byId("status").textContent = "Cancellation requested; waiting for current operation to stop.";
  } catch (error) { byId("status").textContent = error.message; }
});
byId("download").addEventListener("click", () => {
  if (!patch) return;
  const url = URL.createObjectURL(new Blob([patch], {type: "text/plain;charset=utf-8"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = "issue-fix-" + sourceCommit.slice(0, 12) + ".patch";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
