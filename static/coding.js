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
  for (const id of ["external-sessions", "external-export", "external-import", "external-files", "external-focus", "external-profile", "external-scope", "external-manual", "external-provider", "external-response", "external-export-approved", "external-import-approved"]) byId(id).disabled = value;
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
    "Issue #" + item.issue + " — " + (item.last_model || "saved") + " — base " + item.revision.slice(0, 8) + " — " + item.id.slice(0, 8), item.id));
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

let externalBusy = false;
function lockExternal(value) {
  externalBusy = value;
  busy(value);
  byId("stop").disabled = true;
}
function externalIssue() {
  const issue = Number(byId("issue").value);
  if (!Number.isInteger(issue) || issue < 1 || issue > 1000000) throw new Error("Enter a valid issue number.");
  activeToken = byId("token").value.trim();
  return issue;
}
function showIssueRoadmap(roadmap, checksPassed) {
  const section = byId("external-roadmap");
  section.hidden = !roadmap;
  if (!roadmap) return;
  const current = (roadmap.items || []).find(item => item.id === roadmap.current_id);
  byId("external-current-task").textContent = "Current / next task: " + roadmap.current_task;
  const count = roadmap.reported_done || 0;
  const total = roadmap.total || 0;
  byId("external-coverage").textContent = count + " / " + total + " requirements reported complete (not independently verified)" +
    (checksPassed ? " · Standard checks passed" : "");
  const bar = byId("external-coverage-bar");
  bar.max = Math.max(1, total);
  bar.value = count;
  bar.hidden = !total;
  const list = byId("external-roadmap-items");
  list.replaceChildren();
  for (const item of roadmap.items || []) {
    const li = document.createElement("li");
    const label = item.status === "reported_done" ? "Reported complete" :
      item.status === "blocked" ? "Blocked" : item.status === "pending" ? "Pending" : "Not yet reported";
    li.textContent = (item === current ? "CURRENT — " : "") + label + " · " + item.text;
    if (item.evidence) {
      const evidence = document.createElement("p");
      evidence.textContent = item.evidence;
      li.appendChild(evidence);
    }
    list.appendChild(li);
  }
}
function showSavedRoadmap() {
  const saved = checkpoints.find(c => c.id === byId("checkpoint").value);
  showIssueRoadmap(saved?.issue_progress, saved?.checks_passed);
}
function showExternalReport(summary, checks) {
  const details = byId("external-details");
  details.open = false;
  details.hidden = !summary && !checks;
  byId("external-summary").textContent = summary || "";
  byId("external-summary").hidden = !summary;
  byId("external-checks").hidden = !checks;
  byId("external-checks").textContent = checks ? JSON.stringify(checks, null, 2) : "";
}
function externalNextStatus(result) {
  if (result.status === "checks_failed" || (result.checks && result.checks.exit_code !== 0))
    return "Checks failed. Generate latest prompt to repair this session.";
  if (result.status === "untested")
    return "Checks could not finish. Review test details before continuing.";
  if (result.checks?.exit_code === 0 && result.issue_progress?.total > 0 &&
      result.issue_progress.reported_done === result.issue_progress.total)
    return "All requirements reported complete. Review the patch and verify the feature before publication.";
  if (result.status === "no_changes")
    return "No edits this round. Generate latest prompt to continue with updated context.";
  if (result.status === "partial_review" && result.checks?.exit_code === 0)
    return "Checks passed. Generate latest prompt to continue.";
  return "Review the development report before continuing.";
}
function clearExternalPrompt() {
  byId("external-prompt").value = "";
  byId("external-copy").disabled = true;
  byId("external-version").textContent = "";
  byId("external-import-approved").checked = false;
}
byId("external-manual").addEventListener("change", () => {
  byId("external-files").readOnly = !byId("external-manual").checked;
});
byId("checkpoint").addEventListener("change", () => { clearExternalPrompt(); showSavedRoadmap(); });
byId("issue").addEventListener("change", () => { clearExternalPrompt(); showIssueRoadmap(null); });
byId("external-sessions").addEventListener("click", async () => {
  if (externalBusy) return;
  activeToken = byId("token").value.trim();
  lockExternal(true);
  try {
    const selected = byId("checkpoint").value;
    await refreshCheckpoints(selected);
    if (!selected) {
      const latest = checkpoints.find(c => c.issue === Number(byId("issue").value));
      if (latest) byId("checkpoint").value = latest.id;
    }
    clearExternalPrompt();
    showExternalReport("", null);
    showSavedRoadmap();
    byId("external-status").textContent = "Saved sessions loaded. Select your existing checkpoint.";
  } catch (error) {
    byId("external-status").textContent = error.message;
  } finally { lockExternal(false); }
});
byId("external-export").addEventListener("click", async () => {
  if (externalBusy) return;
  try {
    const issue = externalIssue();
    if (!byId("external-export-approved").checked) throw new Error("Approve preparing the selected source first.");
    lockExternal(true);
    const paths = byId("external-files").value.split(/\n/).map(x => x.trim()).filter(Boolean);
    const result = await api("/api/coding/external/export", "POST", {
      issue, checkpoint_id: byId("checkpoint").value || null,
      paths: byId("external-manual").checked ? paths : null,
      focus: byId("external-focus").value.trim(), profile: byId("external-profile").value, work_scope: byId("external-scope").value, approved: true
    });
    await refreshCheckpoints(result.session_id);
    byId("external-files").value = result.paths.join("\n");
    byId("external-budget").textContent = "Prompt: " + Math.ceil(result.prompt_bytes / 1000) + " / " + Math.ceil(result.prompt_budget / 1000) + " KB\n" +
      "Files shown as exact excerpts: " + ((result.partial_paths || []).join(", ") || "none");
    const omitted = result.omitted_paths || [];
    byId("external-omitted").hidden = !omitted.length;
    byId("external-omitted").textContent = omitted.length ?
      "Related files not included in this package (scope or size limit):\n" + omitted.join("\n") +
      "\nThe next automatic package can prioritize omitted files if the developer requests them." : "";
    byId("external-available").textContent = result.available_paths.join("\n");
    byId("external-prompt").value = result.prompt;
    byId("external-version").textContent = "Base: " + result.base_commit + "\nWorkspace: " + result.workspace_version +
      "\nPrompt: " + result.package_id + "\nChanged files: " + (result.changed_paths.join(", ") || "none");
    byId("external-copy").disabled = false;
    byId("external-response").value = "";
    byId("external-import-approved").checked = false;
    byId("external-export-approved").checked = false;
    showExternalReport("", result.checks);
    showIssueRoadmap(result.issue_progress, result.checks?.exit_code === 0);
    byId("external-status").textContent = "Latest prompt generated for issue #" + issue + ". Review, copy, and paste into any web developer.";
  } catch (error) {
    byId("external-status").textContent = error.message;
  } finally { lockExternal(false); }
});
byId("external-copy").addEventListener("click", async () => {
  const field = byId("external-prompt");
  try {
    await navigator.clipboard.writeText(field.value);
    byId("external-status").textContent = "Prompt copied. Paste into your chosen provider.";
  } catch (_) {
    field.focus();
    field.select();
    byId("external-status").textContent = "Copy the selected prompt manually.";
  }
});
byId("external-import").addEventListener("click", async () => {
  if (externalBusy) return;
  try {
    const issue = externalIssue();
    const checkpoint = byId("checkpoint").value;
    if (!checkpoint || !byId("external-import-approved").checked) throw new Error("Select the saved session and approve isolated testing.");
    const response = byId("external-response").value.trim();
    const provider = byId("external-provider").value.trim();
    if (!response || !provider) throw new Error("Enter the provider label and paste its complete JSON response.");
    lockExternal(true);
    byId("external-status").textContent = "Validating response and running isolated checks…";
    const result = await api("/api/coding/external/import", "POST", {
      issue, checkpoint_id: checkpoint, response, provider, approved: true
    });
    await refreshCheckpoints(result.checkpoint_id);
    const priorPaths = byId("external-files").value.split(/\n/).map(x => x.trim()).filter(Boolean);
    if (byId("external-manual").checked) {
      byId("external-files").value = [...new Set([...priorPaths, ...result.changed_paths])].join("\n");
    } else {
      byId("external-files").value = "";
    }
    byId("external-focus").value = "";
    byId("external-omitted").hidden = true;
    byId("external-budget").textContent = "";
    clearExternalPrompt();
    byId("external-response").value = "";
    byId("external-version").textContent = "Base: " + result.source_commit + "\nLatest workspace: " + result.workspace_version +
      "\nChanged files: " + (result.changed_paths.join(", ") || "none");
    byId("external-status").textContent = externalNextStatus(result);
    showIssueRoadmap(result.issue_progress, result.checks?.exit_code === 0);
    showExternalReport([result.message, result.summary].filter(Boolean).join("\n\n"), result.checks);
    patch = result.patch || "";
    sourceCommit = result.source_commit || "";
    byId("review").hidden = !patch;
    byId("result").textContent = result.checks?.exit_code === 0 ?
      "Standard checks passed. Issue coverage still requires review." : "Saved proposed changes are not verified.";
    byId("commit").textContent = "Source commit: " + sourceCommit;
    byId("patch").textContent = patch;
  } catch (error) {
    byId("external-status").textContent = error.message + " Reload saved sessions if the request was interrupted.";
  } finally {
    byId("external-import-approved").checked = false;
    lockExternal(false);
  }
});
