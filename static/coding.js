"use strict";
const byId = id => document.getElementById(id);
let runId = null, patch = "", pollTimer = null, sourceCommit = "";
let activeToken = "";
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
}
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
    byId("status").textContent = result.message || ("Run ended: " + run.status);
    patch = result.patch || "";
    sourceCommit = result.source_commit || "";
    byId("review").hidden = !patch;
    if (patch) {
      byId("result").textContent = (run.status === "review" ? "Standard checks returned exit code 0. Review before applying." :
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
    const run = await api("/api/coding/start", "POST", {issue, approved: true});
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
