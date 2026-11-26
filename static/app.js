const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const messages = document.getElementById("messages");
const welcome = document.getElementById("welcome");
const history = document.getElementById("history");
const modelButton = document.querySelector(".model-button");
const imageInput = document.getElementById("image-input");
const imagePreview = document.getElementById("image-preview");
const attachImageButton = document.getElementById("attach-image-button");

let pendingImages = [];
const MAX_IMAGES = 4;
const MAX_IMAGE_SIZE = 6 * 1024 * 1024;

const composerTools = document.querySelector(".composer-tools");
composerTools.querySelector("span")?.remove();
const toolPickerButton = document.createElement("button");
toolPickerButton.type = "button";
toolPickerButton.className = "tool-picker-button";
toolPickerButton.textContent = "Tools ▾";

const toolPickerMenu = document.createElement("div");
toolPickerMenu.className = "tool-picker-menu";
toolPickerMenu.hidden = true;
toolPickerMenu.innerHTML = `
  <div class="tool-picker-heading">
    <strong>Tools</strong>
    <button type="button" class="tool-picker-close">×</button>
  </div>
  <div class="tool-picker-list">Loading...</div>
`;

composerTools.append(toolPickerButton, toolPickerMenu);

const selectedTools = new Set();

function renderImagePreview() {
  imagePreview.querySelectorAll("img").forEach(image => {
    if (image.src.startsWith("blob:")) URL.revokeObjectURL(image.src);
  });
  imagePreview.innerHTML = "";
  imagePreview.hidden = pendingImages.length === 0;

  pendingImages.forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "image-preview-item";

    const image = document.createElement("img");
    image.src = URL.createObjectURL(file);
    image.alt = file.name;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Remove image";
    remove.addEventListener("click", () => {
      pendingImages.splice(index, 1);
      renderImagePreview();
    });

    item.append(image, remove);
    imagePreview.appendChild(item);
  });
}

attachImageButton.addEventListener("click", () => imageInput.click());

imageInput.addEventListener("change", () => {
  const accepted = Array.from(imageInput.files || []);
  const remaining = MAX_IMAGES - pendingImages.length;

  if (accepted.length > remaining) {
    alert(`You can attach up to ${MAX_IMAGES} images per message.`);
  }

  for (const file of accepted.slice(0, remaining)) {
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      alert(`${file.name}: only PNG, JPEG, and WEBP images are supported.`);
      continue;
    }
    if (file.size > MAX_IMAGE_SIZE) {
      alert(`${file.name}: each image must be 6 MB or smaller.`);
      continue;
    }
    pendingImages.push(file);
  }

  imageInput.value = "";
  renderImagePreview();
});

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}


toolPickerButton.addEventListener("click", async () => {
  toolPickerMenu.hidden = !toolPickerMenu.hidden;
  if (!toolPickerMenu.hidden && !toolPickerMenu.querySelector("input")) {
    await refreshComposerTools();
  }
});

toolPickerMenu.querySelector(".tool-picker-close").addEventListener("click", () => {
  toolPickerMenu.hidden = true;

});

document.addEventListener("pointerdown", event => {
  if (!toolPickerMenu.hidden && !toolPickerMenu.contains(event.target)) {
    toolPickerMenu.hidden = true;
  }
}, true);

function capability(name) {
  const id = name.replace(/^mcp_/, "");
  if (id.includes("calendar")) return "Calendar actions";
  if (id.includes("google_task")) return "Tasks";
  if (id.includes("gmail")) return "Email";
  if (id.includes("conversation")) return "Memory";
  if (id.startsWith("web_")) return "Web search";
  if (/file|document/.test(id)) return "Documents & files";
  if (/system_info|current_time/.test(id)) return "System";
  return "Other tools";
}

function groupTools(tools) {
  const groups = new Map();
  for (const tool of tools) {
    const name = capability(tool.name);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(tool);
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b));
}

function updateToolPickerLabel() {
  const count = new Set([...selectedTools].map(capability)).size;
  toolPickerButton.textContent = count ? `Tools (${count}) ▾` : "Tools (off) ▾";
}

async function refreshComposerTools() {
  const list = toolPickerMenu.querySelector(".tool-picker-list");
  try {
    const response = await fetch("/api/tools");
    if (!response.ok) throw new Error("Could not load tools");
    const tools = await response.json();
    selectedTools.clear();
    list.replaceChildren();
    for (const tool of tools) if (tool.enabled) selectedTools.add(tool.name);
    for (const [name, members] of groupTools(tools)) {
      const label = document.createElement("label");
      label.className = "tool-picker-option";
      const box = document.createElement("input");
      box.type = "checkbox";
      const available = members.filter(tool => tool.enabled);
      const count = members.filter(tool => selectedTools.has(tool.name)).length;
      box.checked = available.length > 0 && count === available.length;
      box.indeterminate = count > 0 && count < available.length;
      box.disabled = !available.length;
      label.append(box, name);
      list.append(label);
      box.addEventListener("change", () => {
        for (const tool of available) {
          if (box.checked) selectedTools.add(tool.name);
          else selectedTools.delete(tool.name);
        }
        updateToolPickerLabel();
      });
    }
    if (!tools.length) list.textContent = "No tools available.";
    updateToolPickerLabel();
  } catch (error) {
    list.textContent = error.message;
  }
}

let currentConversationId = null;
let busy = false;
let selectedModel = "";
let automaticModel = true;
const freeCatalogs = new Map();
let catalogRequest = 0;
let selectedProvider = "groq";
let providerConfigs = {};

const providerSelector = document.createElement("select");
providerSelector.id = "provider-selector";
providerSelector.setAttribute("aria-label", "Chat provider");
providerSelector.innerHTML = `
  <option value="groq">Groq</option>
  <option value="openrouter">OpenRouter</option>
  <option value="gemini">Google Gemini</option>
  <option value="__find__">Find new provider…</option>
`;
form.parentElement.insertBefore(providerSelector, form);

const modelSelector = document.createElement("select");
modelSelector.id = "model-selector";
modelSelector.setAttribute("aria-label", "Chat model");
const discoverOption = "__check_free_models__";
const modelDialog = document.getElementById("model-discovery");

function openModelDiscovery() {
  document.getElementById("discovery-heading").textContent = (providerConfigs[selectedProvider]?.name || selectedProvider) + " free models";
  for (const link of modelDialog.querySelectorAll("[data-provider]")) {
    link.closest("li").hidden = link.dataset.provider !== selectedProvider;
  }
  modelDialog.showModal();
  checkCatalog(true);
}
modelButton.addEventListener("click", openModelDiscovery);

function getSelectedModelInfo() {
  const provider = providerConfigs[selectedProvider];
  return (provider?.models || []).find(model => model.id === selectedModel) ||
    freeCatalogs.get(selectedProvider)?.models.find(model => model.id === selectedModel);
}

const checkModels = document.getElementById("check-models");
const catalogStatus = document.getElementById("catalog-status");
const catalogResults = document.getElementById("catalog-results");
const priority = document.getElementById("model-priority");
let discoveredModels = [];
let newModelIds = new Set();


function renderDiscoveredModels() {
  catalogResults.replaceChildren();
  const score = model => {
    if (priority.value === "context") return model.context_length;
    if (priority.value === "created") return model.created;
    return Number(model.capabilities[priority.value]);
  };
  const sorted = priority.value === "practical" ? discoveredModels : [...discoveredModels].sort((a, b) =>
    score(b) - score(a) || b.created - a.created || a.id.localeCompare(b.id)
  );
  const current = getSelectedModelInfo();
  if (sorted.length) {
    const comparison = document.createElement("p");
    comparison.textContent = `Current: ${current?.name || selectedModel || "No model selected"}`;
    if (current?.context_length) comparison.textContent += ` · ${current.context_length.toLocaleString()} context`;
    catalogResults.append(comparison);
  }
  for (const model of sorted) {
    const row = document.createElement("article");
    const title = document.createElement("strong");
    title.textContent = model.name + (model.id === discoveredModels[0]?.id ? " · Top practical pick" : "") + (newModelIds.has(model.id) ? " · New since last check" : "");
    const info = document.createElement("small");
    const facts = [];
    if (current && selectedModel !== model.id) {
      if (current.context_length && model.context_length > current.context_length) facts.push("Longer context than current");
      if (current.capabilities?.tools === false && model.capabilities.tools) facts.push("Adds tool calling");
      if (current.capabilities?.image === false && model.capabilities.image) facts.push("Adds image input");
    }
    if (model.context_length) facts.push(`${model.context_length.toLocaleString()} context`);
    if (model.capabilities.tools) facts.push("Tool calling");
    if (model.capabilities.image) facts.push("Image input");
    if (model.created) facts.push(`Listed ${new Date(model.created * 1000).toLocaleDateString()}`);
    info.textContent = facts.join(" · ");
    const button = document.createElement("button");
    button.className = "refresh-usage";
    button.type = "button";
    const active = model.id === selectedModel;
    const configured = freeCatalogs.get(selectedProvider)?.configured;
    button.textContent = configured ? (active ? "Selected" : "Use in chat") : "Connect " + providerConfigs[selectedProvider]?.name;
    button.disabled = active && configured;
    button.onclick = () => {
      if (!configured) {
        const guide = document.getElementById("key-guide");
        guide.open = true;
        guide.scrollIntoView({block: "center"});
        guide.querySelector(`[data-provider="${selectedProvider}"]`).focus({preventScroll: true});
        return;
      }
      automaticModel = false;
      selectedModel = model.id;
      populateModelSelector();
      providerStatus.textContent = "● API key configured";
      providerStatus.className = "provider-ready";
      renderDiscoveredModels();
      modelDialog.close();
      document.querySelector('#mobile-navigation [data-page="chat"]').click();
    };
    row.append(title, info, button);
    catalogResults.append(row);
  }
}

async function checkCatalog(showResults) {
  const provider = selectedProvider;
  const name = providerConfigs[provider]?.name || provider;
  const request = ++catalogRequest;
  checkModels.disabled = true;
  discoveredModels = [];
  catalogResults.replaceChildren();
  catalogStatus.textContent = "Checking " + name + "…";
  try {
    const response = await fetch("/api/free-models?provider=" + encodeURIComponent(provider), {cache: "no-store"});
    if (!response.ok) throw new Error("Could not verify " + name + "’s free models. Try again.");
    const data = await response.json();
    if (request !== catalogRequest || provider !== selectedProvider) return;
    freeCatalogs.set(provider, data);
    discoveredModels = data.models;
    newModelIds = new Set();
    try {
      const key = "last-" + provider + "-free-models";
      const previous = JSON.parse(localStorage.getItem(key));
      const ids = discoveredModels.map(model => model.id);
      if (Array.isArray(previous)) newModelIds = new Set(ids.filter(id => !previous.includes(id)));
      if (showResults) localStorage.setItem(key, JSON.stringify(ids));
    } catch { /* Browser storage is optional. */ }
    populateModelSelector();
    providerStatus.textContent = data.configured ? (selectedModel ? "● API key configured" : "No free default available") : "API key missing";
    catalogStatus.textContent = discoveredModels.length
      ? name + " · " + discoveredModels.length + " free-tier chat models. Account tier and rate limits apply."
      : "No qualifying chat models found for " + name + ".";
    renderDiscoveredModels();
  } catch (error) {
    if (request !== catalogRequest || provider !== selectedProvider) return;
    catalogStatus.textContent = error.message;
    if (!freeCatalogs.has(provider) && automaticModel) providerStatus.textContent = error.message;
  } finally {
    if (request === catalogRequest) checkModels.disabled = false;
  }
}
checkModels.addEventListener("click", () => checkCatalog(true));
priority.addEventListener("change", renderDiscoveredModels);

function populateModelSelector() {
  const provider = providerConfigs[selectedProvider];
  const catalog = freeCatalogs.get(selectedProvider);
  const free = catalog?.models || [];
  const recommended = catalog?.configured ? free[0] : null;
  const choices = new Map();
  for (const model of [...(catalog?.configured ? free : []), ...(provider?.models || [])]) {
    if (!choices.has(model.id)) choices.set(model.id, model);
  }
  if (!automaticModel && !choices.has(selectedModel)) automaticModel = true;
  if (automaticModel) selectedModel = recommended?.id || "";
  modelSelector.replaceChildren(new Option(
    "Default (best available)" + (recommended ? " · " + recommended.name : ""), "__default__"
  ));
  for (const model of choices.values()) {
    const tier = free.some(item => item.id === model.id) ? " · free tier" : " · check pricing";
    modelSelector.add(new Option((model.name || model.id) + tier, model.id));
  }
  modelSelector.add(new Option("Check for free models…", discoverOption));
  modelSelector.value = automaticModel ? "__default__" : selectedModel;
  updateModelDisplay();
}

form.parentElement.insertBefore(modelSelector, form);

const providerStatus = document.createElement("span");
providerStatus.id = "composer-provider-status";
providerStatus.textContent = "Checking providers...";
form.parentElement.insertBefore(providerStatus, form);

function updateModelDisplay() {
  const provider = providerConfigs[selectedProvider];
  const providerName = provider?.name || selectedProvider;
  const model = getSelectedModelInfo()?.name || selectedModel || "No model selected";

  modelButton.textContent = `${providerName} · ${model}`;
  modelButton.title = `Active provider: ${providerName}\nModel: ${model}`;
}

function selectProvider(id) {
  selectedProvider = providerSelector.value = id;
  automaticModel = true;
  populateModelSelector();
  const configured = providerConfigs[id]?.configured;
  providerStatus.textContent = configured ? "Checking best available model…" : "API key missing";
  if (configured) checkCatalog(false);
}

providerSelector.addEventListener("change", () => {
  if (providerSelector.value === "__find__") {
    providerSelector.value = selectedProvider;
    document.getElementById("provider-discovery").showModal();
    findProviders();
  } else {
    selectProvider(providerSelector.value);
  }
});

async function findProviders() {
  const list = document.getElementById("provider-offers");
  const refresh = document.getElementById("find-providers");
  if (refresh.disabled) return;
  refresh.disabled = true;
  list.textContent = "Checking the public provider directory…";
  try {
    const response = await fetch("/api/free-providers", {cache: "no-store"});
    if (!response.ok) throw new Error("Provider search failed. Try again.");
    const {providers} = await response.json();
    list.replaceChildren();
    let position = 0;
    let unknownHeading = false;
    for (const offer of providers) {
      if (!offer.comparable && !unknownHeading) {
        const heading = document.createElement("h3");
        heading.textContent = "Daily cap unverified · sorted by model capabilities";
        list.append(heading);
        unknownHeading = true;
      }
      const row = document.createElement("article");
      const title = document.createElement("strong");
      title.textContent = (offer.comparable ? (++position) + ". " : "") + offer.name;
      const strengths = document.createElement("p");
      strengths.textContent = offer.example ? offer.example + " · Good for " + offer.strengths + "." : offer.strengths;
      const limits = document.createElement("p");
      limits.textContent = offer.limits;
      const link = document.createElement("a");
      link.href = offer.limit_url || offer.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Provider limits";
      row.append(title, strengths, limits, link);
      if (offer.supported) {
        const use = document.createElement("button");
        use.className = "refresh-usage";
        use.textContent = "View models";
        use.onclick = () => {
          document.getElementById("provider-discovery").close();
          selectedProvider = providerSelector.value = offer.id;
          automaticModel = true;
          populateModelSelector();
          openModelDiscovery();
        };
        row.append(" ", use);
      } else {
        row.append(" · External provider");
      }
      list.append(row);
    }
  } catch (error) { list.textContent = error.message; }
  finally { refresh.disabled = false; }
}
document.getElementById("find-providers").addEventListener("click", findProviders);

modelSelector.addEventListener("change", () => {
  if (modelSelector.value === discoverOption) {
    modelSelector.value = automaticModel ? "__default__" : selectedModel;
    openModelDiscovery();
    return;
  }
  automaticModel = modelSelector.value === "__default__";
  if (!automaticModel) selectedModel = modelSelector.value;
  populateModelSelector();
  if (automaticModel && !selectedModel) openModelDiscovery();
});

async function refreshProviderStatus() {
  try {
    const response = await fetch("/api/providers");
    if (!response.ok) throw new Error("Status unavailable");

    const providers = await response.json();

    providerConfigs = {};
    document.getElementById("key-status").textContent = providers.map(p => p.name + (p.configured ? " · key configured" : " · key missing")).join(" | ");
    providers.forEach(provider => {
      providerConfigs[provider.id] = provider;
    });

    const firstConnected = providers.find(p => p.configured);
    if (!providerConfigs[selectedProvider]?.configured && firstConnected) {
      selectedProvider = providerSelector.value = firstConnected.id;
    }
    const current = providerConfigs[selectedProvider];

    providerStatus.textContent = current
      ? (current.configured ? "● API key configured" : "○ API key missing")
      : "Provider unavailable";

    providerStatus.className = current?.configured
      ? "provider-ready"
      : "provider-missing";

    populateModelSelector();
    if (current?.configured) void checkCatalog(false);

  } catch (error) {
    providerStatus.textContent = "Status unavailable";
    console.error(error);
  }
}

function scrollToBottom() {
  const area = document.getElementById("conversation");
  area.scrollTop = area.scrollHeight;
}

function renderTaskApprovalCard(message, pending) {
  const task = pending.proposal;
  const headings = {
    create: "Create Google Task",
    update: "Update Google Task",
    delete: "Delete Google Task"
  };
  if (!pending.pending_id || !task || !Object.hasOwn(headings, task.action)) return;

  const card = document.createElement("section");
  card.className = "calendar-approval-card";
  const heading = document.createElement("h3");
  heading.textContent = headings[task.action];
  card.appendChild(heading);

  function addDetail(label, value) {
    const row = document.createElement("p");
    const name = document.createElement("strong");
    name.textContent = `${label}: `;
    row.append(name, document.createTextNode(value));
    card.appendChild(row);
  }

  function display(field, value) {
    if (value == null || value === "") return "(none)";
    if (field === "status") {
      if (value === "completed") return "Completed";
      if (value === "needsAction") return "Incomplete";
    }
    return String(value);
  }

  addDetail("List", task.task_list_title);
  addDetail("Task", task.task_title);
  const labels = { title: "Title", notes: "Notes", due: "Due date", status: "Status" };
  for (const [field, label] of Object.entries(labels)) {
    if (task.action === "delete") {
      if (field !== "title" && task.current?.[field]) {
        addDetail(label, display(field, task.current[field]));
      }
      continue;
    }
    if (!Object.hasOwn(task.changes || {}, field)) continue;
    if (field === "title" && task.action === "create") continue;
    const next = display(field, task.changes[field]);
    addDetail(label, task.action === "update"
      ? `${display(field, task.current?.[field])} → ${next}`
      : next);
  }

  const approve = document.createElement("button");
  const decline = document.createElement("button");
  approve.type = decline.type = "button";
  approve.textContent = task.action === "delete" ? "Approve deletion" : "Approve";
  decline.textContent = "Decline";
  const status = document.createElement("p");
  status.setAttribute("role", "status");

  async function decide(approved) {
    if (approve.disabled) return;
    approve.disabled = decline.disabled = true;
    status.textContent = approved ? "Applying change…" : "Declining…";
    try {
      const response = await fetch("/api/google/tasks/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pending_id: pending.pending_id, approved })
      });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.detail || "Could not confirm the decision.";
        return;
      }
      status.textContent = result.status === "declined"
        ? "Declined. No change was made."
        : result.status === "completed"
          ? "Google Task change completed."
          : "Could not confirm the decision.";
    } catch {
      status.textContent = "Connection interrupted. Check the task list before retrying.";
    }
  }

  approve.addEventListener("click", () => decide(true));
  decline.addEventListener("click", () => decide(false));
  card.append(approve, decline, status);
  message.appendChild(card);
}

function renderMessageContent(message, text) {
  const markers = [
    { marker: "<!--CALENDAR_APPROVAL:", type: "calendar" },
    { marker: "<!--TASK_APPROVAL:", type: "task" },
    { marker: "<!--GMAIL_APPROVAL:", type: "gmail" }
  ];
  const foundMarkers = markers
    .map(item => ({ ...item, start: text.indexOf(item.marker) }))
    .filter(item => item.start !== -1)
    .sort((a, b) => a.start - b.start);
  const found = foundMarkers[0];
  const markerStart = found ? found.start : -1;
  let proposalType = found?.type || "";
  let visibleText = text;
  let proposal = null;

  if (found) {
    const jsonStart = markerStart + found.marker.length;
    const markerEnd = text.indexOf("-->", jsonStart);
    if (markerEnd !== -1) {
      try {
        proposal = JSON.parse(text.slice(jsonStart, markerEnd));
        visibleText = text.slice(0, markerStart).trimEnd();
      } catch (error) {
        console.error("Invalid approval proposal payload", error);
      }
    }
  }

  message.replaceChildren();

  if (window.marked && window.DOMPurify) {
    message.innerHTML = DOMPurify.sanitize(marked.parse(visibleText || ""));
  } else {
    message.textContent = visibleText;
  }

  if (proposalType === "task" && proposal) {
    renderTaskApprovalCard(message, proposal);
    return;
  }

  const pendingIds = proposalType === "calendar" && proposal
    ? (Array.isArray(proposal.pending_ids)
        ? proposal.pending_ids
        : proposal.pending_id
          ? [proposal.pending_id]
          : [])
    : [];

  const proposals = proposalType === "calendar" && proposal
    ? (Array.isArray(proposal.proposals)
        ? proposal.proposals
        : proposal.pending_id
          ? [proposal]
          : [])
    : [];

  if (pendingIds.length && proposals.length) {
    const card = document.createElement("section");
    card.className = "calendar-approval-card";
    card.style.cssText = "margin-top:14px;padding:16px;border:1px solid var(--border-color,#888);border-radius:12px";

    const heading = document.createElement("strong");
    heading.textContent = `Review ${proposals.length} calendar change${proposals.length === 1 ? "" : "s"}`;
    card.appendChild(heading);

    const CALENDAR_TIME_ZONE = "America/Vancouver";

    function getCalendarDateParts(value) {
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return null;
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: CALENDAR_TIME_ZONE,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23"
      }).formatToParts(date);
      return Object.fromEntries(parts.map(part => [part.type, part.value]));
    }

    function formatDate(value, allDay = false) {
      if (!value) return "";
      if (allDay) {
        const parts = value.split("-").map(Number);
        const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2], 12));
        return Number.isNaN(date.getTime())
          ? value
          : date.toLocaleDateString([], { timeZone: "UTC", dateStyle: "full" });
      }
      const date = new Date(value);
      return Number.isNaN(date.getTime())
        ? value
        : date.toLocaleString([], {
            timeZone: CALENDAR_TIME_ZONE,
            dateStyle: "full",
            timeStyle: "short"
          });
    }

    const proposalTimeInputs = [];

    function toDateTimeLocal(value) {
      if (!value) return "";
      const parts = getCalendarDateParts(value);
      if (!parts) return "";
      return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
    }

    proposals.forEach((item, index) => {
      const action = item.action || "create";
      const event = item.event || item.proposal?.event || item;
      const section = document.createElement("div");
      section.style.cssText = "margin-top:12px;padding-top:10px;border-top:1px solid var(--border-color,#888)";

      const title = document.createElement("strong");
      title.textContent = `${index + 1}. ${
        { create: "Create", update: "Update", delete: "Delete" }[action] || "Change"
      }`;
      section.appendChild(title);

      if (action === "create" || action === "update") {
        const titleLabel = document.createElement("label");
        titleLabel.textContent = "Event title";
        titleLabel.style.cssText = "display:block;margin-top:10px;font-size:0.9em";
        const titleInput = document.createElement("input");
        titleInput.type = "text";
        titleInput.maxLength = 200;
        titleInput.required = true;
        titleInput.value = event.summary || item.summary || "";
        titleInput.placeholder = "Enter event title";
        titleInput.setAttribute("aria-label", `Event ${index + 1} title`);
        titleInput.style.cssText = "display:block;box-sizing:border-box;width:100%;max-width:480px;margin-top:6px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font:inherit";
        titleLabel.appendChild(titleInput);
        section.appendChild(titleLabel);

        const descriptionLabel = document.createElement("label");
        descriptionLabel.textContent = "Description (optional)";
        descriptionLabel.style.cssText = "display:block;margin-top:10px;font-size:0.9em";
        const descriptionInput = document.createElement("textarea");
        descriptionInput.maxLength = 5000;
        descriptionInput.rows = 2;
        descriptionInput.value = event.description || "";
        descriptionInput.placeholder = "Add notes or details";
        descriptionInput.style.cssText = "display:block;box-sizing:border-box;width:100%;max-width:480px;margin-top:6px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font:inherit;resize:vertical";
        descriptionLabel.appendChild(descriptionInput);
        section.appendChild(descriptionLabel);

        const locationLabel = document.createElement("label");
        locationLabel.textContent = "Location (optional)";
        locationLabel.style.cssText = "display:block;margin-top:10px;font-size:0.9em";
        const locationInput = document.createElement("input");
        locationInput.type = "text";
        locationInput.maxLength = 500;
        locationInput.value = event.location || "";
        locationInput.placeholder = "Add a location";
        locationInput.style.cssText = "display:block;box-sizing:border-box;width:100%;max-width:480px;margin-top:6px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg);color:var(--text);font:inherit";
        locationLabel.appendChild(locationInput);
        section.appendChild(locationLabel);
        item._calendarEditor = { titleInput, descriptionInput, locationInput };
      }

      const details = document.createElement("div");
      details.style.cssText = "margin-top:6px;white-space:pre-line";
      const lines = [];

      if (action === "create" || action === "update") {
        const allDay = Boolean(
          event.start?.date && !event.start?.dateTime &&
          event.end?.date && !event.end?.dateTime
        );
        const startValue = event.start?.dateTime || event.start?.date || event.start_time;
        const endValue = event.end?.dateTime || event.end?.date || event.end_time;
        if (startValue) lines.push(`Start: ${formatDate(startValue, allDay)}`);
        if (endValue) lines.push(
          allDay
            ? `End date (exclusive): ${formatDate(endValue, true)}`
            : `End: ${formatDate(endValue)}`
        );
        if (event.recurrence?.length) {
          const rule = event.recurrence[0] || "";
          const frequency = /FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)/i.exec(rule)?.[1]?.toUpperCase();
          const dayNames = {
            MO: "Monday", TU: "Tuesday", WE: "Wednesday", TH: "Thursday",
            FR: "Friday", SA: "Saturday", SU: "Sunday"
          };
          const days = /BYDAY=([^;]+)/i.exec(rule)?.[1]
            ?.split(",").map(day => dayNames[day.toUpperCase()]).filter(Boolean) || [];
          const descriptions = {
            DAILY: "Every day",
            WEEKLY: days.length ? `Every week on ${days.join(", ")}` : "Every week",
            MONTHLY: "Every month",
            YEARLY: "Every year"
          };
          if (frequency) lines.push(`Repeats: ${descriptions[frequency]}`);
        }
        if (event.location) lines.push(`Location: ${event.location}`);
        if (event.description) lines.push(`Description: ${event.description}`);
      } else if (action === "delete") {
        lines.push("This event will be permanently deleted from Google Calendar.");
        if (event.summary || item.summary) lines.push(event.summary || item.summary);
      }

      details.textContent = lines.join("\n");
      section.appendChild(details);

      if (action === "create" || action === "update") {
        const allDay = Boolean(
          event.start?.date && !event.start?.dateTime &&
          event.end?.date && !event.end?.dateTime
        );
        function getDateAndTime(value, fallbackDate = "") {
          const local = toDateTimeLocal(value);
          if (local) {
            return {
              date: local.slice(0, 10),
              hour: String(Number(local.slice(11, 13)) % 12 || 12),
              minute: local.slice(14, 16),
              period: Number(local.slice(11, 13)) >= 12 ? "PM" : "AM"
            };
          }
          return { date: fallbackDate, hour: "", minute: "", period: "" };
        }

        const startOriginal = getDateAndTime(
          event.start?.dateTime || event.start_time,
          event.start?.date || ""
        );
        const endOriginal = getDateAndTime(
          event.end?.dateTime || event.end_time,
          event.end?.date || ""
        );

        const startInput = document.createElement("input");
        startInput.type = "date";
        startInput.value = event.start?.date || startOriginal.date || "";
        startInput.setAttribute("aria-label", `Event ${index + 1} start date`);

        const endInput = document.createElement("input");
        endInput.type = "date";
        endInput.value = event.end?.date || endOriginal.date || "";
        endInput.setAttribute("aria-label", `Event ${index + 1} end date`);

        const dateInputStyle = "display:block;width:100%;max-width:320px;margin-top:8px;padding:9px;border:1px solid var(--border-color,#888);border-radius:8px;background:var(--input-bg,transparent);color:inherit";
        startInput.style.cssText = dateInputStyle;
        endInput.style.cssText = dateInputStyle;

        function makeTimePicker(initial, label) {
          const wrap = document.createElement("div");
          wrap.style.cssText = "display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;max-width:320px;margin-top:8px";

          function makeSelect(ariaLabel, values, selected, placeholder) {
            const select = document.createElement("select");
            select.setAttribute("aria-label", ariaLabel);
            select.style.cssText = "min-width:0;width:100%;padding:9px;border:1px solid var(--border-color,#888);border-radius:8px;background:var(--input-bg,transparent);color:inherit";
            const blank = document.createElement("option");
            blank.value = "";
            blank.textContent = placeholder;
            select.appendChild(blank);
            values.forEach(value => {
              const option = document.createElement("option");
              option.value = value;
              option.textContent = value;
              select.appendChild(option);
            });
            select.value = selected || "";
            return select;
          }

          const hour = makeSelect(`${label} hour`,
            Array.from({ length: 12 }, (_, i) => String(i + 1)),
            initial.hour, "Hour");
          const minute = document.createElement("input");
          minute.type = "number";
          minute.min = "0";
          minute.max = "59";
          minute.step = "1";
          minute.placeholder = "Minute";
          minute.value = initial.minute || "";
          minute.inputMode = "numeric";
          minute.setAttribute("aria-label", `${label} minute`);
          minute.style.cssText = "min-width:0;width:100%;box-sizing:border-box;padding:9px;border:1px solid var(--border-color,#888);border-radius:8px;background:var(--input-bg,transparent);color:inherit";
          const period = makeSelect(`${label} AM or PM`,
            ["AM", "PM"], initial.period, "AM/PM");
          wrap.append(hour, minute, period);
          return {
            element: wrap,
            controls: [hour, minute, period],
            hasInvalidMinute() {
              if (!minute.value.trim()) return false;
              const value = Number(minute.value);
              return !Number.isInteger(value) || value < 0 || value > 59;
            },
            values() {
              const minuteValue = minute.value.trim();
              if (
                !hour.value ||
                !minuteValue ||
                !period.value ||
                this.hasInvalidMinute()
              ) return "";
              let h = Number(hour.value) % 12;
              if (period.value === "PM") h += 12;
              return `${String(h).padStart(2, "0")}:${minuteValue.padStart(2, "0")}`;
            }
          };
        }

        const startTime = makeTimePicker(startOriginal, `Event ${index + 1} start`);
        const endTime = makeTimePicker(endOriginal, `Event ${index + 1} end`);

        const recurrenceRules = Array.isArray(event.recurrence) ? event.recurrence : [];
        const originalRule = recurrenceRules[0] || "";
        const originalFrequency = /FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)/i.exec(originalRule)?.[1]?.toLowerCase() || "none";
        const recurrenceLabel = document.createElement("label");
        recurrenceLabel.textContent = "Repeat";
        recurrenceLabel.style.cssText = "display:block;margin-top:12px;font-size:0.9em";
        const recurrenceSelect = document.createElement("select");
        recurrenceSelect.setAttribute("aria-label", `Event ${index + 1} repeat frequency`);
        recurrenceSelect.style.cssText = "display:block;width:100%;max-width:320px;margin-top:8px;padding:9px;border:1px solid var(--border-color,#888);border-radius:8px;background:var(--input-bg,transparent);color:inherit";
        [
          ["none", "Does not repeat"],
          ["daily", "Every day"],
          ["weekly", "Every week"],
          ["monthly", "Every month"],
          ["yearly", "Every year"]
        ].forEach(([value, label]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          recurrenceSelect.appendChild(option);
        });
        recurrenceSelect.value = originalFrequency;
        recurrenceLabel.appendChild(recurrenceSelect);

        const weekdayWrap = document.createElement("div");
        weekdayWrap.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;margin-top:9px";
        const weekdayInputs = {};
        const weekdayOptions = [
          ["MO", "Mon"], ["TU", "Tue"], ["WE", "Wed"], ["TH", "Thu"],
          ["FR", "Fri"], ["SA", "Sat"], ["SU", "Sun"]
        ];
        const originalDays = /BYDAY=([^;]+)/i.exec(originalRule)?.[1]
          ?.split(",").map(day => day.toUpperCase()) || [];
        if (!originalDays.length && originalFrequency === "weekly") {
          const anchor = new Date(`${startInput.value || startOriginal.date}T12:00:00`);
          if (!Number.isNaN(anchor.getTime())) {
            originalDays.push(["SU", "MO", "TU", "WE", "TH", "FR", "SA"][anchor.getDay()]);
          }
        }
        weekdayOptions.forEach(([value, label]) => {
          const dayLabel = document.createElement("label");
          dayLabel.style.cssText = "display:inline-flex;align-items:center;gap:4px;font-size:0.85em";
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.value = value;
          checkbox.checked = originalDays.includes(value);
          dayLabel.append(checkbox, document.createTextNode(label));
          weekdayWrap.appendChild(dayLabel);
          weekdayInputs[value] = checkbox;
        });
        function updateWeekdayVisibility() {
          weekdayWrap.hidden = recurrenceSelect.value !== "weekly";
        }
        recurrenceSelect.addEventListener("change", updateWeekdayVisibility);
        updateWeekdayVisibility();
        section.append(recurrenceLabel, weekdayWrap);

        const recurrenceEditor = {
          values() {
            const frequency = recurrenceSelect.value.toUpperCase();
            if (frequency === "NONE") return [];
            if (frequency === "WEEKLY") {
              const days = weekdayOptions
                .filter(([value]) => weekdayInputs[value].checked)
                .map(([value]) => value);
              if (!days.length) return null;
              return [`RRULE:FREQ=WEEKLY;BYDAY=${days.join(",")}`];
            }
            return [`RRULE:FREQ=${frequency}`];
          }
        };

        const allDayLabel = document.createElement("label");
        allDayLabel.style.cssText = "display:block;margin-top:10px;font-size:0.9em";
        const allDayToggle = document.createElement("input");
        allDayToggle.type = "checkbox";
        allDayToggle.checked = allDay;
        allDayToggle.style.marginRight = "7px";
        allDayLabel.append(allDayToggle, document.createTextNode("All-day event"));

        const startLabel = document.createElement("label");
        startLabel.textContent = "Start date";
        startLabel.style.cssText = "display:block;margin-top:10px;font-size:0.9em";
        startLabel.appendChild(startInput);

        const endLabel = document.createElement("label");
        endLabel.textContent = allDay ? "End date (exclusive)" : "End date";
        endLabel.style.cssText = "display:block;margin-top:8px;font-size:0.9em";
        endLabel.appendChild(endInput);

        const startTimeLabel = document.createElement("label");
        startTimeLabel.textContent = "Start time";
        startTimeLabel.style.cssText = "display:block;margin-top:8px;font-size:0.9em";
        startTimeLabel.appendChild(startTime.element);

        const endTimeLabel = document.createElement("label");
        endTimeLabel.textContent = "End time";
        endTimeLabel.style.cssText = "display:block;margin-top:8px;font-size:0.9em";
        endTimeLabel.appendChild(endTime.element);

        function updateTimeVisibility() {
          startTimeLabel.hidden = allDayToggle.checked;
          endTimeLabel.hidden = allDayToggle.checked;
          endLabel.firstChild.textContent = allDayToggle.checked
            ? "End date (exclusive)"
            : "End date";
        }
        allDayToggle.addEventListener("change", updateTimeVisibility);
        updateTimeVisibility();

        section.append(
          allDayLabel, startLabel, startTimeLabel, endLabel, endTimeLabel
        );
        proposalTimeInputs.push({
          startInput, endInput, startTime, endTime, allDayToggle,
          recurrenceEditor, recurrenceSelect, weekdayWrap,
          section, action
        });

        function refreshDetails() {
          const currentLines = [];
          const isAllDay = allDayToggle.checked;
          const startClock = isAllDay ? "" : startTime.values();
          const endClock = isAllDay ? "" : endTime.values();

          if (startInput.value) {
            currentLines.push(`Start: ${formatDate(
              startClock ? `${startInput.value}T${startClock}` : startInput.value,
              !startClock
            )}`);
          }
          if (endInput.value) {
            currentLines.push(isAllDay
              ? `End date (exclusive): ${formatDate(endInput.value, true)}`
              : `End: ${formatDate(
                  endClock ? `${endInput.value}T${endClock}` : endInput.value,
                  !endClock
                )}`);
          }

          const selectedRecurrence = recurrenceEditor.values();
          if (selectedRecurrence?.length) {
            const rule = selectedRecurrence[0];
            const frequency = /FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)/i.exec(rule)?.[1]?.toUpperCase();
            const dayNames = {
              MO: "Monday", TU: "Tuesday", WE: "Wednesday", TH: "Thursday",
              FR: "Friday", SA: "Saturday", SU: "Sunday"
            };
            const days = /BYDAY=([^;]+)/i.exec(rule)?.[1]
              ?.split(",").map(day => dayNames[day.toUpperCase()]).filter(Boolean) || [];
            const descriptions = {
              DAILY: "Every day",
              WEEKLY: days.length ? `Every week on ${days.join(", ")}` : "Every week",
              MONTHLY: "Every month",
              YEARLY: "Every year"
            };
            if (frequency) currentLines.push(`Repeats: ${descriptions[frequency]}`);
          }

          const editor = item._calendarEditor || {};
          const location = editor.locationInput?.value.trim();
          const description = editor.descriptionInput?.value.trim();
          if (location) currentLines.push(`Location: ${location}`);
          if (description) currentLines.push(`Description: ${description}`);
          details.textContent = currentLines.join("\n");
        }

        [startInput, endInput].forEach(input => {
          input.addEventListener("input", refreshDetails);
          input.addEventListener("change", refreshDetails);
        });
        [startTime, endTime].forEach(picker => {
          picker.controls.forEach(control => {
            control.addEventListener("input", refreshDetails);
            control.addEventListener("change", refreshDetails);
          });
        });
        [recurrenceSelect, weekdayWrap, allDayToggle].forEach(element => {
          element.addEventListener("change", refreshDetails);
        });
        const editor = item._calendarEditor || {};
        [editor.descriptionInput, editor.locationInput].forEach(input => {
          input?.addEventListener("input", refreshDetails);
        });
        refreshDetails();
      } else {
        proposalTimeInputs.push(null);
      }

      item._calendarSection = section;
      card.appendChild(section);
    });

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin-top:14px";

    const approve = document.createElement("button");
    approve.type = "button";
    approve.textContent = `Approve all ${pendingIds.length} change${pendingIds.length === 1 ? "" : "s"}`;

    const decline = document.createElement("button");
    decline.type = "button";
    decline.textContent = "Decline all";
    decline.className = "secondary";

    const status = document.createElement("p");
    status.style.margin = "10px 0 0";
    status.hidden = true;

    async function decide(approved) {
      let hasValidationErrors = false;
      if (approved) {
        proposalTimeInputs.forEach((fields, index) => {
          if (!fields || fields.action === "delete") return;
          const editor = proposals[index]._calendarEditor || {};
          const isTimed = !fields.allDayToggle.checked;
          const invalidTitle = !editor.titleInput?.value.trim();
          const invalidStartDate = !fields.startInput.value;
          const invalidEndDate = !fields.endInput.value || (
            fields.allDayToggle.checked &&
            fields.endInput.value <= fields.startInput.value
          );
          const invalidStartTime = isTimed && !fields.startTime.values();
          const invalidEndTime = isTimed && !fields.endTime.values();
          const invalidMinute = isTimed && (
            fields.startTime.hasInvalidMinute() || fields.endTime.hasInvalidMinute()
          );
          const invalidRecurrence = fields.recurrenceEditor.values() === null;
          const invalid = invalidTitle || invalidStartDate || invalidEndDate ||
            invalidStartTime || invalidEndTime || invalidMinute || invalidRecurrence;
          hasValidationErrors ||= invalid;

          function markInvalid(element, value) {
            if (!element) return;
            element.style.borderColor = value ? "#d93025" : "";
            element.style.outline = value ? "2px solid rgba(217,48,37,.3)" : "";
            element.setAttribute("aria-invalid", String(Boolean(value)));
          }

          markInvalid(editor.titleInput, invalidTitle);
          markInvalid(fields.startInput, invalidStartDate);
          markInvalid(fields.endInput, invalidEndDate);
          markInvalid(fields.startTime.element, invalidStartTime || invalidMinute);
          markInvalid(fields.endTime.element, invalidEndTime || invalidMinute);
          fields.startTime.controls.forEach((control, index) => {
            markInvalid(control, invalidStartTime || (invalidMinute && index === 1));
          });
          fields.endTime.controls.forEach((control, index) => {
            markInvalid(control, invalidEndTime || (invalidMinute && index === 1));
          });
          markInvalid(fields.recurrenceSelect, invalidRecurrence);
          markInvalid(fields.weekdayWrap, invalidRecurrence);
          fields.section.style.outline = invalid ? "2px solid #d93025" : "";
          fields.section.style.outlineOffset = invalid ? "3px" : "";
          fields.section.style.borderRadius = invalid ? "8px" : "";
          fields.section.style.background = invalid ? "rgba(217,48,37,.05)" : "";
        });
      }

      if (approved && hasValidationErrors) {
        status.hidden = false;
        status.textContent = "Please fix the highlighted events and fields before approving. Timed events need complete start and end times; weekly events need at least one weekday.";
        return;
      }

      approve.disabled = true;
      decline.disabled = true;
      status.hidden = false;
      status.textContent = approved
        ? "Applying calendar changes…"
        : "Declining calendar changes…";

      try {
        const response = await fetch("/api/google/calendar/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pending_ids: pendingIds,
            approved,
            times: proposalTimeInputs.map((fields, index) => {
              if (!fields) return {};
              const editor = proposals[index]._calendarEditor || {};
              const common = {
                summary: editor.titleInput?.value.trim() || "",
                description: editor.descriptionInput?.value.trim() || "",
                location: editor.locationInput?.value.trim() || "",
                recurrence: fields.recurrenceEditor.values()
              };
              return fields.allDayToggle.checked
                ? {
                    ...common,
                    start_date: fields.startInput.value,
                    end_date: fields.endInput.value
                  }
                : {
                    ...common,
                    start_time: `${fields.startInput.value}T${fields.startTime.values()}`,
                    end_time: `${fields.endInput.value}T${fields.endTime.values()}`
                  };
            })
          })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not process this proposal.");

        if (!approved) {
          actions.remove();
          status.textContent = `${data.declined || pendingIds.length} calendar change(s) declined.`;
          return;
        }

        const results = data.results || [];
        const succeeded = results.filter(item => item.success).length;
        const failed = results.length - succeeded;
        status.textContent = `${succeeded} change(s) completed${failed ? `; ${failed} failed` : ""}.`;

        results.forEach((item, index) => {
          const resultLine = document.createElement("div");
          resultLine.style.cssText = "margin-top:6px";
          resultLine.textContent = item.success
            ? `${item.summary || item.action || "Calendar change"}: completed.`
            : `Calendar change failed: ${item.error || "Unknown error"}`;
          card.appendChild(resultLine);

          if (item.success && item.html_link) {
            const link = document.createElement("a");
            link.href = item.html_link;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "Open event in Google Calendar";
            link.style.display = "block";
            card.appendChild(link);
          }
        });

        if (failed) {
          const failedIndices = results
            .map((item, index) => item.success ? -1 : index)
            .filter(index => index >= 0);
          const failedIds = failedIndices.map(index => pendingIds[index]);
          const failedProposals = failedIndices.map(index => proposals[index]);
          const failedInputs = failedIndices.map(index => proposalTimeInputs[index]);

          proposals.forEach((item, index) => {
            if (!failedIndices.includes(index)) item._calendarSection?.remove();
          });

          pendingIds.splice(0, pendingIds.length, ...failedIds);
          proposals.splice(0, proposals.length, ...failedProposals);
          proposalTimeInputs.splice(0, proposalTimeInputs.length, ...failedInputs);
          heading.textContent = `Review ${failed} remaining calendar change${failed === 1 ? "" : "s"}`;
          approve.textContent = `Retry ${failed} failed change${failed === 1 ? "" : "s"}`;
          approve.disabled = false;
          decline.disabled = false;
          status.textContent += ". Failed changes remain available to retry.";
        } else {
          actions.remove();
        }
      } catch (error) {
        status.textContent = error.message;
        approve.disabled = false;
        decline.disabled = false;
      }
    }

    approve.addEventListener("click", () => decide(true));
    decline.addEventListener("click", () => decide(false));
    actions.append(approve, decline);
    card.append(actions, status);
    message.appendChild(card);
  }

  if (proposalType === "gmail" && proposal?.pending_id && proposal?.message) {
    const card = document.createElement("section");
    card.className = "calendar-approval-card";
    card.style.cssText = "margin-top:14px;padding:16px;border:1px solid var(--border-color,#888);border-radius:12px";

    const heading = document.createElement("strong");
    heading.textContent = "Review and edit email before sending";
    card.appendChild(heading);

    const email = proposal.message;
    const fields = {};
    const fieldSpecs = [
      ["To", "to", "email"],
      ["Cc", "cc", "text"],
      ["Bcc", "bcc", "text"],
      ["Reply-To", "reply_to", "email"],
      ["Subject", "subject", "text"]
    ];

    function addField(labelText, key, type = "text") {
      const wrapper = document.createElement("label");
      wrapper.style.cssText = "display:block;margin-top:10px;font-size:13px";
      wrapper.textContent = labelText;
      const input = document.createElement("input");
      input.type = type;
      input.value = email[key] || "";
      input.style.cssText = "display:block;box-sizing:border-box;width:100%;margin-top:4px;padding:9px;border:1px solid var(--border-color,#888);border-radius:7px;background:var(--input-bg,var(--background));color:inherit;font:inherit";
      input.autocomplete = "off";
      wrapper.appendChild(input);
      card.appendChild(wrapper);
      fields[key] = input;
    }

    fieldSpecs.forEach(([label, key, type]) => addField(label, key, type));

    const bodyLabel = document.createElement("label");
    bodyLabel.textContent = "Message";
    bodyLabel.style.cssText = "display:block;margin-top:12px;font-size:13px";
    const bodyInput = document.createElement("textarea");
    bodyInput.value = email.body || "";
    bodyInput.rows = 8;
    bodyInput.style.cssText = "display:block;box-sizing:border-box;width:100%;margin-top:4px;padding:10px;border:1px solid var(--border-color,#888);border-radius:7px;background:var(--input-bg,var(--background));color:inherit;font:inherit;resize:vertical";
    bodyLabel.appendChild(bodyInput);
    card.appendChild(bodyLabel);

    const attachmentHeading = document.createElement("strong");
    attachmentHeading.textContent = "Attachments";
    attachmentHeading.style.cssText = "display:block;margin-top:16px";
    card.appendChild(attachmentHeading);

    const attachmentHelp = document.createElement("p");
    attachmentHelp.textContent = "Select files from Documents / My AI Agent. You can add or remove attachments before sending.";
    attachmentHelp.style.cssText = "margin:5px 0;font-size:12px;opacity:.75";
    card.appendChild(attachmentHelp);

    const attachmentList = document.createElement("div");
    attachmentList.style.cssText = "max-height:220px;overflow:auto;border:1px solid var(--border-color,#888);border-radius:8px;padding:8px";
    card.appendChild(attachmentList);

    const selectedAttachments = new Set(
      Array.isArray(email.attachments) ? email.attachments : []
    );

    function renderAttachments(items) {
      attachmentList.replaceChildren();
      const available = new Map(items.map(item => [item.path, item]));
      for (const path of selectedAttachments) {
        if (!available.has(path)) {
          available.set(path, { path, name: path, size: null });
        }
      }

      if (!available.size) {
        const empty = document.createElement("p");
        empty.textContent = "No attachable documents found.";
        empty.style.cssText = "margin:4px;font-size:13px;opacity:.7";
        attachmentList.appendChild(empty);
        return;
      }

      for (const item of available.values()) {
        const label = document.createElement("label");
        label.style.cssText = "display:flex;align-items:center;gap:8px;padding:6px 2px;font-size:13px;overflow-wrap:anywhere";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selectedAttachments.has(item.path);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selectedAttachments.add(item.path);
          else selectedAttachments.delete(item.path);
        });
        const name = document.createElement("span");
        name.textContent = item.name;
        name.style.flex = "1";
        const size = document.createElement("span");
        size.textContent = Number.isFinite(item.size)
          ? `${(item.size / 1024).toFixed(1)} KB`
          : "";
        size.style.opacity = ".65";
        label.append(checkbox, name, size);
        attachmentList.appendChild(label);
      }
    }

    renderAttachments([]);
    fetch("/api/google/gmail/attachments")
      .then(response => {
        if (!response.ok) throw new Error("Could not load available documents.");
        return response.json();
      })
      .then(data => renderAttachments(Array.isArray(data.items) ? data.items : []))
      .catch(error => {
        const warning = document.createElement("p");
        warning.textContent = error.message;
        warning.style.cssText = "font-size:12px;color:var(--danger-color,#c44)";
        attachmentList.appendChild(warning);
      });

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;margin-top:14px";

    const approve = document.createElement("button");
    approve.type = "button";
    approve.textContent = "Approve and send";
    const decline = document.createElement("button");
    decline.type = "button";
    decline.textContent = "Decline";
    decline.className = "secondary";

    const status = document.createElement("p");
    status.style.cssText = "margin:10px 0 0;white-space:pre-wrap";
    status.hidden = true;

    async function decide(approved) {
      approve.disabled = true;
      decline.disabled = true;
      status.hidden = false;
      status.textContent = approved ? "Sending email…" : "Declining email…";

      try {
        const response = await fetch("/api/google/gmail/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pending_id: proposal.pending_id,
            approved,
            message: approved ? {
              to: fields.to.value.trim(),
              cc: fields.cc.value.trim(),
              bcc: fields.bcc.value.trim(),
              reply_to: fields.reply_to.value.trim(),
              subject: fields.subject.value.trim(),
              body: bodyInput.value,
              attachments: [...selectedAttachments]
            } : undefined
          })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Could not process this email.");

        actions.remove();
        status.textContent = approved
          ? `Email sent to ${data.recipient}. ${data.attachment_count || 0} attachment(s) included.`
          : "Email declined; nothing was sent.";
      } catch (error) {
        status.textContent = error.message;
        approve.disabled = false;
        decline.disabled = false;
      }
    }

    approve.addEventListener("click", () => decide(true));
    decline.addEventListener("click", () => decide(false));
    actions.append(approve, decline);
    card.append(actions, status);
    message.appendChild(card);
  }
}

function addMessage(text, role, images = []) {
  welcome.style.display = "none";

  const message = document.createElement("div");
  message.className = `message ${role}`;

  if (role === "agent") {
    renderMessageContent(message, text || "");
  } else if (text) {
    message.textContent = text;
  }

  if (images.length) {
    const gallery = document.createElement("div");
    gallery.className = "message-image-gallery";

    images.forEach(imageData => {
      const image = document.createElement("img");
      image.src = typeof imageData === "string" ? imageData : imageData.data_url;
      image.alt = "Attached image";
      image.loading = "lazy";
      gallery.appendChild(image);
    });

    message.appendChild(gallery);
  }

  messages.appendChild(message);
  scrollToBottom();
  return message;
}

const historyDialog = document.getElementById("history-dialog");
const historyLabel = document.querySelector(".history-label");
document.getElementById("open-history").addEventListener("click", () => {
  historyDialog.append(history);
  historyDialog.showModal();
});

historyDialog.addEventListener("close", () => historyLabel.after(history));
window.matchMedia("(max-width: 700px)").addEventListener("change", event => {
  if (!event.matches && historyDialog.open) historyDialog.close();
});

async function refreshHistory() {
  const response = await fetch("/api/conversations");
  if (!response.ok) throw new Error("Could not load conversation history");

  const conversations = await response.json();
  const items = document.createDocumentFragment();

  conversations.forEach(conversation => {
    const item = document.createElement("button");
    item.className = "history-item";
    item.textContent = conversation.title;
    item.title = conversation.title;

    if (conversation.id === currentConversationId) {
      item.classList.add("selected");
    }

    item.addEventListener("click", () => openConversation(conversation.id));
    items.appendChild(item);
  });
  history.replaceChildren(items);
}

async function openConversation(id) {
  if (busy) return;
  showChat();

  const response = await fetch(`/api/conversations/${id}`);
  if (!response.ok) {
    alert("Could not open this conversation.");
    return;
  }

  const data = await response.json();

  if (historyDialog.open) {
    historyDialog.close();
    document.querySelectorAll("#mobile-navigation [data-page]").forEach(button => {
      const active = button.dataset.page === "chat";
      button.classList.toggle("active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
  }
  currentConversationId = id;
  messages.innerHTML = "";
  welcome.style.display = data.messages.length ? "none" : "block";

  data.messages.forEach(message => {
    addMessage(
      message.content,
      message.role === "user" ? "user" : "agent",
      message.images || []
    );
  });

  await refreshHistory();
}

async function newChat() {
  if (busy) return;
  showChat();

  const response = await fetch("/api/conversations", {
    method: "POST"
  });

  if (!response.ok) {
    alert("Could not create a new conversation.");
    return;
  }

  const data = await response.json();
  currentConversationId = data.id;

  messages.innerHTML = "";
  welcome.style.display = "block";
  input.value = "";

  await refreshHistory();
  input.focus();
}

async function sendMessage(text) {
  if ((!text.trim() && !pendingImages.length) || busy) return;

  if (!currentConversationId) {
    await newChat();
  }

  const userMessage = text.trim();
  const attachedFiles = [...pendingImages];

  if (attachedFiles.length) {
    const modelInfo = getSelectedModelInfo();
    if (!modelInfo?.capabilities?.image) {
      alert("The selected model does not support image input. Please choose a vision-capable model.");
      return;
    }
  }

  let imageData = [];
  try {
    imageData = await Promise.all(attachedFiles.map(fileToDataUrl));
  } catch (error) {
    alert(error.message);
    return;
  }

  addMessage(userMessage, "user", imageData);
  pendingImages = [];
  renderImagePreview();

  input.value = "";
  input.disabled = true;
  busy = true;

  const responseMessage = addMessage("Thinking...", "agent");
  responseMessage.classList.add("loading");

  try {
    if (!selectedModel) {
      throw new Error("Please select a model before sending a message.");
    }

    let toolsForThisMessage = Array.from(selectedTools);

    try {
      if (!userMessage && imageData.length) {
        throw new Error("Skip extension assessment for image-only messages.");
      }
      const decisionResponse = await fetch("/api/ai-decide-extensions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: currentConversationId,
          message: userMessage || "Please analyze the attached image(s).",
          provider: selectedProvider,
          model: selectedModel
        })
      });

      if (decisionResponse.ok) {
        const decision = await decisionResponse.json();

        if (Array.isArray(decision.required_extensions)) {
          toolsForThisMessage = decision.needs_extensions
            ? toolsForThisMessage.filter(name => decision.required_extensions.includes(name))
            : [];
        }
      } else {
        console.warn("Extension assessment unavailable; using manual tool selection.");
      }
    } catch (assessmentError) {
      console.warn("Extension assessment failed; using manual tool selection.", assessmentError);
    }

    let retryWithMissingTool = true;
    let responseRaw = "";
    let isRetry = false;

    while (true) {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: currentConversationId,
          message: userMessage,
          images: imageData,
          provider: selectedProvider,
          model: selectedModel,
          selected_tools: toolsForThisMessage,
          retry_without_saving: isRetry
        })
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "The request failed");
      }
      if (!response.body) {
        throw new Error("Streaming is not supported by this browser");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      responseRaw = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        responseRaw += decoder.decode(value, { stream: true });
      }
      responseRaw += decoder.decode();

      const missingToolMatch = responseRaw.match(
        /attempted to call tool ['"]([^'"]+)['"] which was not in request\.tools/i
      );

      if (missingToolMatch && retryWithMissingTool && selectedTools.has(missingToolMatch[1])) {
        const missingTool = missingToolMatch[1];
        const approved = window.confirm(
          `The model tried to use an extension that was not included: ${capability(missingTool)}\n\n` +
          "Add this extension to the current request and retry?"
        );

        if (approved) {
          const toolsResponse = await fetch("/api/tools");
          if (!toolsResponse.ok) {
            throw new Error("Could not load available extensions.");
          }
          const availableTools = await toolsResponse.json();
          if (!availableTools.some(tool => tool.name === missingTool && tool.enabled)) {
            throw new Error(`The requested extension ${missingTool} is not available.`);
          }

          toolsForThisMessage = [...new Set([...toolsForThisMessage, missingTool])];
          retryWithMissingTool = false;
          isRetry = true;
          responseMessage.dataset.raw = "";
          responseMessage.classList.add("loading");
          responseMessage.textContent = "Retrying with the missing extension…";
          continue;
        }
      }

      const followReply = conversationPage.scrollHeight - conversationPage.clientHeight - conversationPage.scrollTop <= 8;
      renderMessageContent(responseMessage, responseRaw);
      if (!responseMessage.textContent.trim()) {
        responseMessage.textContent = "The model returned an empty response.";
      }
      if (followReply) scrollToBottom();
      break;
    }

    await refreshHistory();

  } catch (error) {
    responseMessage.textContent = "Error: " + error.message;
  } finally {
    input.disabled = false;
    input.focus({ preventScroll: true });
    busy = false;
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function useSuggestion(text) {
  sendMessage(text);
}

async function initialize() {
  try {
    await refreshProviderStatus();
    await refreshComposerTools();



    const response = await fetch("/api/conversations");
    if (!response.ok) throw new Error("Could not load history");

    await newChat();
  } catch (error) {
    console.error(error);
    alert("Could not initialize conversation history: " + error.message);
  }
}

initialize();

const chatNav = document.getElementById("chat-nav");
const usageNav = document.getElementById("usage-nav");
const toolsNav = document.getElementById("tools-nav");
const filesNav = document.getElementById("files-nav");
const toolsPage = document.getElementById("tools-page");
const filesPage = document.getElementById("files-page");
const usagePage = document.getElementById("usage-page");
const conversationPage = document.getElementById("conversation");
const composerArea = document.querySelector(".composer-area");
const breadcrumb = document.querySelector(".breadcrumb");

async function showUsage() {
  conversationPage.style.display = "none";
  composerArea.style.display = "none";
  toolsPage.style.display = "none";
  filesPage.style.display = "none";
  usagePage.style.display = "block";

  chatNav.classList.remove("active");
  toolsNav.classList.remove("active");
  usageNav.classList.add("active");
  breadcrumb.innerHTML = "Usage";

  await refreshUsage();
}

function showChat() {
  usagePage.style.display = "none";
  toolsPage.style.display = "none";
  filesPage.style.display = "none";
  conversationPage.style.display = "flex";
  composerArea.style.display = "";

  usageNav.classList.remove("active");
  toolsNav.classList.remove("active");
  chatNav.classList.add("active");
  breadcrumb.innerHTML = "Chat";

  refreshHistory();
}

function updateResetCountdowns() {
  document.querySelectorAll(".usage-reset").forEach(cell => {
    const duration = cell.dataset.duration;
    const updatedAt = Date.parse(cell.dataset.updated);

    if (!duration || !Number.isFinite(updatedAt)) {
      cell.textContent = "Not exposed";
      return;
    }

    const match = duration.match(/(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?/);

    if (!match || !match[0]) {
      cell.textContent = duration;
      return;
    }

    const seconds = Number(match[1] || 0) * 3600
      + Number(match[2] || 0) * 60
      + Number(match[3] || 0);

    const remaining = Math.max(0, Math.ceil(
      seconds - (Date.now() - updatedAt) / 1000
    ));

    if (remaining === 0) {
      cell.textContent = "Reset window elapsed";
    } else {
      const hours = Math.floor(remaining / 3600);
      const minutes = Math.floor((remaining % 3600) / 60);
      const secs = remaining % 60;

      const parts = [];
      if (hours) parts.push(`${hours}h`);
      if (hours || minutes) parts.push(`${minutes}m`);
      parts.push(`${secs.toString().padStart(2, "0")}s`);

      cell.textContent = parts.join(" ");
    }
  });
}

setInterval(updateResetCountdowns, 1000);

async function refreshUsage() {
  const summary = document.getElementById("usage-summary");
  const rows = document.getElementById("usage-rows");
  const providerStatus = document.getElementById("provider-status");

  summary.textContent = "Refreshing live usage...";
  rows.innerHTML = "";

  try {
    const refreshResponse = await fetch("/api/usage/refresh", {
      method: "POST"
    });

    if (!refreshResponse.ok) {
      const error = await refreshResponse.json();
      throw new Error(error.detail || "Could not refresh provider usage");
    }

    const response = await fetch("/api/usage");
    if (!response.ok) throw new Error("Could not load usage data");

    const result = await response.json();
    const data = Array.isArray(result.daily) ? result.daily : [];
    const turns = Array.isArray(result.turns) ? result.turns : [];

    const totalTokens = data.reduce(
      (sum, item) => sum + (item.tokens_used || 0), 0
    );

    const knownRemaining = data
      .filter(item => item.requests_remaining !== null)
      .reduce((sum, item) => sum + item.requests_remaining, 0);

    const knownModels = data.filter(
      item => item.requests_remaining !== null
    ).length;

    const totalTurns = turns.reduce(
      (sum, item) => sum + item.turns, 0
    );

    const averageTokens = totalTurns
      ? Math.round(turns.reduce(
          (sum, item) => sum + item.total_tokens, 0
        ) / totalTurns)
      : null;

    summary.innerHTML = `
      <div class="usage-card">
        <span>Models tracked</span>
        <strong>${data.length}</strong>
      </div>
      <div class="usage-card">
        <span>Known requests remaining</span>
        <strong>${knownModels ? knownRemaining.toLocaleString() : "—"}</strong>
      </div>
      <div class="usage-card">
        <span>Total tokens recorded today</span>
        <strong>${totalTokens.toLocaleString()}</strong>
      </div>
      <div class="usage-card">
        <span>Average tokens per turn</span>
        <strong>${averageTokens === null ? "—" : averageTokens.toLocaleString()}</strong>
      </div>
      <div class="usage-card">
        <span>Turns recorded</span>
        <strong>${totalTurns.toLocaleString()}</strong>
      </div>
    `;

    function isDailyLimitReached(item) {
      const checks = [
        [item.rpd_used, item.rpd_limit],
        [item.tpd_used, item.tpd_limit]
      ].filter(([used, limit]) =>
        used !== null && used !== undefined &&
        limit !== null && limit !== undefined
      );

      return checks.length > 0 &&
        checks.some(([used, limit]) => Number(used) >= Number(limit));
    }

    function hasKnownDailyLimit(item) {
      return (
        item.rpd_limit !== null && item.rpd_limit !== undefined
      ) || (
        item.tpd_limit !== null && item.tpd_limit !== undefined
      );
    }

    const providerGroups = {};
    data.forEach(item => {
      (providerGroups[item.provider] ||= []).push(item);
    });

    const providerLabels = {
      gemini: "Google Gemini",
      groq: "Groq",
      openrouter: "OpenRouter"
    };

    providerStatus.innerHTML = "";

    ["gemini", "groq", "openrouter"].forEach(providerId => {
      const models = providerGroups[providerId] || [];
      const card = document.createElement("div");
      card.className = "provider-status-card";

      const header = document.createElement("div");
      header.className = "provider-status-header";

      const heading = document.createElement("strong");
      heading.textContent = providerLabels[providerId];
      header.appendChild(heading);

      const detail = document.createElement("p");
      detail.className = "provider-status-detail";

      const status = document.createElement("span");
      status.className = "provider-status-value";

      if (providerId === "openrouter") {
        const freeModels = models.filter(item =>
          item.model.endsWith(":free")
        );

        if (!freeModels.length) {
          status.textContent = "Unknown";
          detail.textContent = "No free-model quota data recorded yet.";
        } else {
          const limits = freeModels
            .map(item => item.rpd_limit)
            .filter(value => value !== null && value !== undefined)
            .map(Number);

          const limit = limits.length ? Math.max(...limits) : null;
          const used = freeModels.reduce(
            (sum, item) => sum + Number(item.rpd_used || 0), 0
          );

          if (limit === null) {
            status.textContent = "Limit not exposed";
            detail.textContent =
              `${used.toLocaleString()} free-model requests recorded today.`;
          } else {
            const remaining = Math.max(0, limit - used);
            status.textContent = remaining > 0
              ? "Available"
              : "Daily limit reached";
            detail.textContent =
              `${used.toLocaleString()} / ${limit.toLocaleString()} shared free-model requests today · ${remaining.toLocaleString()} remaining`;
          }
        }
      } else {
        const knownModels = models.filter(hasKnownDailyLimit);
        const availableModels = knownModels.filter(
          item => !isDailyLimitReached(item)
        );
        const exhaustedModels = knownModels.length - availableModels.length;

        if (!knownModels.length) {
          status.textContent = "Unknown";
          detail.textContent = models.length
            ? "No model daily limits are available to determine status."
            : "No usage data recorded for this provider yet.";
        } else {
          status.textContent = availableModels.length
            ? "Available"
            : "Unavailable";
          detail.textContent = "No daily provider cap";
        }
      }

      status.classList.add("provider-status-value");
      status.classList.toggle(
        "status-available",
        status.textContent === "Available"
      );
      status.classList.toggle(
        "status-unavailable",
        status.textContent === "Unavailable" ||
        status.textContent === "Daily limit reached"
      );

      header.appendChild(status);
      card.appendChild(header);
      card.appendChild(detail);
      providerStatus.appendChild(card);
    });

    function quotaText(used, limit) {
      if (used === null || used === undefined) return "—";
      const usage = Number(used).toLocaleString();
      return limit === null || limit === undefined
        ? `${usage} / —`
        : `${usage} / ${Number(limit).toLocaleString()}`;
    }

    data.forEach(item => {
      const provider = providerConfigs[item.provider];
      const providerName = provider?.name || item.provider;
      const modelInfo = provider?.models?.find(model => model.id === item.model);
      const modelNameOverrides = {
        "openai/gpt-oss-120b": "GPT OSS 120B",
        "nvidia/nemotron-3.5-super-vl:free": "Nemotron 3.5 Super VL",
        "gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
        "gemma-4-26b-a4b-it": "Gemma 4 26B A4B IT"
      };
      const modelName = modelNameOverrides[item.model] || modelInfo?.name || item.model;

      const rpm = quotaText(item.rpm_used, item.rpm_limit);
      const tpm = quotaText(item.tpm_used, item.tpm_limit);
      const rpd = quotaText(item.rpd_used, item.rpd_limit);
      const tpd = quotaText(item.tpd_used, item.tpd_limit);

      const modelTurns = turns.find(turn =>
        turn.provider === item.provider && turn.model === item.model
      );

      const turnCount = modelTurns ? modelTurns.turns : 0;
      const modelAverage = modelTurns
        ? Math.round(modelTurns.total_tokens / turnCount)
        : null;

      const average = modelAverage === null
        ? "—"
        : `${modelAverage.toLocaleString()} (${turnCount} ${turnCount === 1 ? "turn" : "turns"})`;

      const card = document.createElement("article");
      card.className = "usage-model-card";

      const modelStatus = hasKnownDailyLimit(item)
        ? (isDailyLimitReached(item) ? "Daily limit reached" : "Available")
        : "Limit unknown";

      card.innerHTML = `
        <div class="usage-model-header">
          <div class="usage-model-identity">
            <span class="usage-model-provider"></span>
            <strong class="usage-model-name"></strong>
          </div>
          <span class="usage-model-status"></span>
        </div>
        <div class="usage-model-metrics"></div>
        <div class="usage-model-footer">
          <div class="usage-model-average">
            <span>Average tokens / turn</span>
            <strong></strong>
          </div>
          <div class="usage-model-reset">
            <span>Next reset</span>
            <strong class="usage-reset"></strong>
          </div>
        </div>
      `;

      card.querySelector(".usage-model-provider").textContent = providerName;
      card.querySelector(".usage-model-name").textContent = modelName;

      const statusBadge = card.querySelector(".usage-model-status");
      statusBadge.textContent = modelStatus;
      statusBadge.classList.add(
        modelStatus === "Available" ? "status-available" :
        modelStatus === "Daily limit reached" ? "status-unavailable" :
        "status-unknown"
      );

      const metrics = [
        ["RPM", rpm, "Last 60 seconds"],
        ["TPM", tpm, "Last 60 seconds"],
        ["RPD", rpd, "Today"],
        ["TPD", tpd, "Today"]
      ];

      const metricsContainer = card.querySelector(".usage-model-metrics");
      metrics.forEach(([label, value, period]) => {
        const metric = document.createElement("div");
        metric.className = "usage-model-metric";
        const heading = document.createElement("span");
        heading.textContent = label;
        const amount = document.createElement("strong");
        amount.textContent = value;
        const caption = document.createElement("small");
        caption.textContent = period;
        metric.append(heading, amount, caption);
        metricsContainer.appendChild(metric);
      });

      card.querySelector(".usage-model-average strong").textContent = average;

      const resetCell = card.querySelector(".usage-reset");
      resetCell.dataset.duration = item.requests_reset || "";
      resetCell.dataset.updated = item.updated_at || "";

      rows.appendChild(card);
    });

    updateResetCountdowns();

  } catch (error) {
    summary.textContent = "Could not refresh usage: " + error.message;
  }
}
chatNav.addEventListener("click", showChat);
filesNav.addEventListener("click", showFiles);
usageNav.addEventListener("click", showUsage);
toolsNav.addEventListener("click", showTools);

document.querySelectorAll("#mobile-navigation button[data-page]").forEach(button => {
  button.addEventListener("click", () => {
    const page = button.dataset.page;
    const navigation = {
      chat: chatNav,
      tools: toolsNav,
      files: filesNav,
      usage: usageNav
    };

    navigation[page]?.click();

    document.querySelectorAll("#mobile-navigation button").forEach(item => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-current", active ? "page" : "false");
    });
  });
});

document.getElementById("refresh-usage")
  .addEventListener("click", refreshUsage);
document.getElementById("refresh-tools")
  .addEventListener("click", refreshTools);


async function refreshTools() {
  const summary = document.getElementById("tools-summary");
  const list = document.getElementById("tools-list");
  summary.textContent = "Loading tools...";
  list.innerHTML = "";

  try {
    const response = await fetch("/api/tools");
    if (!response.ok) throw new Error("Could not load available tools");
    const tools = await response.json();
    summary.textContent = "";
    list.replaceChildren();
    if (!tools.length) {
      list.textContent = "No tools are currently available.";
      return;
    }
    for (const [group, members] of groupTools(tools)) {
      const section = document.createElement("details");
      section.className = "capability-group";
      const heading = document.createElement("summary");
      const enabledCount = members.filter(tool => tool.enabled).length;
      heading.textContent = `${group} · ${enabledCount}/${members.length} enabled`;
      const groupToggle = document.createElement("button");
      groupToggle.className = "refresh-usage";
      const enable = enabledCount !== members.length;
      groupToggle.textContent = enable ? "Enable all" : "Disable all";
      groupToggle.setAttribute("aria-label", groupToggle.textContent + " in " + group);
      groupToggle.onclick = async () => {
        groupToggle.disabled = true;
        try {
          const response = await fetch("/api/tool-settings/batch", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({names: members.map(tool => tool.name), enabled: enable})
          });
          if (!response.ok) throw new Error("Could not save these settings. Refresh and try again.");
          await refreshTools();
          await refreshComposerTools();
        } catch (error) { summary.textContent = error.message; groupToggle.disabled = false; }
      };
      const description = document.createElement("p");
      description.textContent = {
        "Calendar actions": "Find events and propose calendar changes.",
        "Tasks": "Find, create, update, and complete Google Tasks.",
        "Email": "Prepare Gmail messages for approval.",
        "Documents & files": "Find and read files, or create a document.",
        "Memory": "Search and reopen past conversations.",
        "System": "Check local time and system information.",
        "Web search": "Search the web and read a page.",
        "Other tools": "Additional tools available to this agent."
      }[group];
      section.append(heading, description, groupToggle);
      list.append(section);
      members.forEach(tool => {
        const card = document.createElement("details");
        card.className = "tool-card";

        const header = document.createElement("summary");
        header.className = "tool-card-header";

        const name = document.createElement("strong");
        name.className = "tool-name";
        name.textContent = tool.name.replace(/^mcp_/, "").replaceAll("_", " ");

        const source = document.createElement("span");
        source.className = `tool-source ${tool.source === "MCP" ? "mcp" : "builtin"}`;
        source.textContent = tool.source;
        header.append(name, source);

        const description = document.createElement("p");
        description.className = "tool-description";
        description.textContent = tool.description || "No description provided.";

        const properties = tool.parameters?.properties || {};
        const required = tool.parameters?.required || [];
        const parameterArea = document.createElement("div");
        parameterArea.className = "tool-parameters";

        const title = document.createElement("span");
        title.className = "tool-parameters-title";
        title.textContent = Object.keys(properties).length ? "Test parameters" : "No parameters required";
        parameterArea.appendChild(title);

        const controls = {};

        Object.entries(properties).forEach(([key, parameter]) => {
          const field = document.createElement("label");
          field.className = "tool-test-field";

          const label = document.createElement("span");
          label.textContent = `${key}${required.includes(key) ? " *" : ""}`;

          let control;
          if (Array.isArray(parameter.enum)) {
            control = document.createElement("select");
            parameter.enum.forEach(value => {
              const option = document.createElement("option");
              option.value = String(value);
              option.textContent = String(value);
              control.appendChild(option);
            });
          } else if (parameter.type === "boolean") {
            control = document.createElement("input");
            control.type = "checkbox";
          } else {
            control = document.createElement("input");
            control.type = ["integer", "number"].includes(parameter.type) ? "number" : "text";
            if (parameter.type === "integer") control.step = "1";
            if (parameter.type === "number") control.step = "any";
            control.placeholder = parameter.description || `Enter ${key}`;
          }

          control.name = key;
          control.required = required.includes(key);
          if (parameter.default !== undefined && parameter.type !== "boolean") {
            control.value = String(parameter.default);
          }

          field.append(label, control);
          parameterArea.appendChild(field);
          controls[key] = { control, schema: parameter };
        });

        const runButton = document.createElement("button");
        runButton.type = "button";
        runButton.className = "tool-run-button";
        runButton.textContent = "Run tool";

        const result = document.createElement("pre");
        result.className = "tool-run-result";
        result.hidden = true;

        runButton.addEventListener("click", async () => {
          const args = {};

          for (const [key, entry] of Object.entries(controls)) {
            const { control, schema } = entry;
            const raw = control.type === "checkbox" ? control.checked : control.value;

            if (control.required && raw === "") {
              control.reportValidity();
              return;
            }

            if (schema.type === "boolean") {
              args[key] = raw;
            } else if (raw !== "") {
              if (schema.type === "integer") {
                const value = Number(raw);
                if (!Number.isInteger(value)) {
                  control.setCustomValidity("Enter a whole number.");
                  control.reportValidity();
                  control.setCustomValidity("");
                  return;
                }
                args[key] = value;
              } else if (schema.type === "number") {
                const value = Number(raw);
                if (!Number.isFinite(value)) return;
                args[key] = value;
              } else {
                args[key] = raw;
              }
            }
          }

          runButton.disabled = true;
          runButton.textContent = "Running...";
          result.hidden = false;
          result.textContent = "Executing...";
          result.classList.remove("error");

          try {
            const response = await fetch(
              `/api/tools/${encodeURIComponent(tool.name)}/run`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(args)
              }
            );
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || "Tool execution failed");
            result.textContent = payload.result ?? "Completed successfully.";
          } catch (error) {
            result.textContent = error.message;
            result.classList.add("error");
          } finally {
            runButton.disabled = tool.enabled === false;
            runButton.textContent = tool.enabled === false
              ? "Tool disabled"
              : "Run tool";
          }
        });

        const toggleLabel = document.createElement("label");
        toggleLabel.className = "tool-enable-toggle";

        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.checked = tool.enabled !== false;
        toggle.setAttribute("aria-label", `Enable ${tool.name}`);

        const toggleText = document.createElement("span");
        toggleText.textContent = toggle.checked ? "Enabled" : "Disabled";

        toggle.addEventListener("change", async () => {
          toggle.disabled = true;
          try {
            const response = await fetch(
              `/api/tools/${encodeURIComponent(tool.name)}/enabled`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: toggle.checked })
              }
            );

            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || "Could not update tool");

            tool.enabled = payload.enabled;
            await refreshTools();
            await refreshComposerTools();
          } catch (error) {
            toggle.checked = !toggle.checked;
            toggleText.textContent = toggle.checked ? "Enabled" : "Disabled";
            result.hidden = false;
            result.textContent = error.message;
            result.classList.add("error");
          } finally {
            toggle.disabled = false;
          }
        });

        toggleLabel.append(toggle, toggleText);
        runButton.disabled = tool.enabled === false;
        runButton.textContent = tool.enabled === false ? "Tool disabled" : "Run tool";
        card.classList.toggle("tool-disabled", tool.enabled === false);

        const schemaDetails = document.createElement("details");
        const schemaLabel = document.createElement("summary");
        schemaLabel.textContent = "Parameter schema";
        const schema = document.createElement("pre");
        schema.textContent = JSON.stringify(tool.parameters || {}, null, 2);
        schemaDetails.append(schemaLabel, schema);
        card.append(header, description, toggleLabel, parameterArea, schemaDetails, runButton, result);
        section.appendChild(card);
      });
    }
  } catch (error) {
    summary.textContent = "Could not load tools: " + error.message;
    list.textContent = "Check that the application and MCP server are running.";
  }
}
async function showTools() {
  conversationPage.style.display = "none";
  composerArea.style.display = "none";
  usagePage.style.display = "none";
  filesPage.style.display = "none";
  toolsPage.style.display = "block";

  chatNav.classList.remove("active");
  usageNav.classList.remove("active");
  toolsNav.classList.add("active");
  breadcrumb.innerHTML = "Tools";

  await refreshTools();
}



let currentFilesPath = "";


async function loadFiles(path = currentFilesPath) {
  const list = document.getElementById("files-list");
  const breadcrumb = document.getElementById("files-breadcrumb");
  const upButton = document.getElementById("files-up");
  const preview = document.getElementById("file-preview");
  const endpoint = "/api/documents";

  list.textContent = "Loading...";
  preview.innerHTML = '<div class="file-preview-empty">Select a file to preview its contents.</div>';

  try {
    const response = await fetch(`${endpoint}?path=${encodeURIComponent(path)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load files");

    currentFilesPath = data.path;
    const rootLabel = "My AI Agent Documents";
    breadcrumb.textContent = rootLabel + (data.path ? " / " + data.path : "");
    upButton.disabled = !data.path;
    upButton.onclick = () => loadFiles(data.parent || "");

    list.replaceChildren();

    if (!data.items.length) {
      list.textContent = "This folder is empty.";
      return;
    }

    data.items.forEach(item => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "file-item";

      const icon = document.createElement("span");
      icon.className = "file-item-icon";
      icon.textContent = item.type === "directory" ? "📁" : "📄";

      const name = document.createElement("span");
      name.className = "file-item-name";
      name.textContent = item.name;

      const meta = document.createElement("small");
      meta.textContent = item.type === "directory" ? "Folder" : formatFileSize(item.size);

      row.append(icon, name, meta);
      row.addEventListener("click", () => {
        if (item.type === "directory") {
          loadFiles(item.path);
        } else {
          previewFile(item.path);
        }
      });
      list.append(row);
    });
  } catch (error) {
    list.textContent = "Error: " + error.message;
  }
}

function formatFileSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

async function previewFile(path) {
  const preview = document.getElementById("file-preview");
  const endpoint = "/api/documents/content";
  preview.textContent = "Loading preview...";

  try {
    const response = await fetch(`${endpoint}?path=${encodeURIComponent(path)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not preview file");

    preview.replaceChildren();

    const heading = document.createElement("div");
    heading.className = "file-preview-heading";

    const name = document.createElement("strong");
    name.textContent = data.name;

    const size = document.createElement("small");
    size.textContent = `${data.path} · ${formatFileSize(data.size)}`;

    heading.append(name, size);

    const extension = data.name.split(".").pop().toLowerCase();
    const content = document.createElement("pre");
    content.className = "file-preview-content";
    content.textContent = data.content;

    const format = document.createElement("small");
    format.className = "file-preview-format";
    format.textContent = extension.toUpperCase() + " · Text preview";

    preview.append(heading, format, content);
  } catch (error) {
    preview.textContent = "Could not preview file: " + error.message;
  }
}

async function showFiles() {
  conversationPage.style.display = "none";
  composerArea.style.display = "none";
  usagePage.style.display = "none";
  toolsPage.style.display = "none";
  filesPage.style.display = "block";

  chatNav.classList.remove("active");
  usageNav.classList.remove("active");
  toolsNav.classList.remove("active");
  filesNav.classList.add("active");

  breadcrumb.innerHTML = "Files";

  await loadFiles("");
}

document.getElementById("refresh-files").addEventListener("click", () => {
  loadFiles();
});


// Google Calendar and Gmail connection controls
async function refreshGoogleCalendarStatus() {
  const status = document.getElementById("google-calendar-status");
  const connect = document.getElementById("google-calendar-connect");

  try {
    const response = await fetch("/api/google/calendar/status");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not check Google Calendar.");

    if (data.connected) {
      status.textContent = `Connected: ${data.calendar}${data.timezone ? ` (${data.timezone})` : ""}`;
      connect.textContent = "Disconnect Google account";
    } else {
      status.textContent = data.error
        ? `Not connected: ${data.error}`
        : "Google Calendar is not connected.";
      connect.textContent = "Connect Google account";
    }
  } catch (error) {
    status.textContent = error.message;
  }
}

async function refreshGoogleGmailStatus() {
  const status = document.getElementById("google-gmail-status");
  const connect = document.getElementById("google-gmail-connect");

  try {
    const response = await fetch("/api/google/gmail/status");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not check Gmail.");

    if (data.connected) {
      status.textContent = `Connected: ${data.email || "Gmail account"}`;
      connect.textContent = "Gmail connected";
      connect.disabled = true;
    } else {
      status.textContent = data.error
        ? `Not connected: ${data.error}`
        : "Gmail is not connected.";
      connect.textContent = "Connect Google account";
      connect.disabled = false;
    }
  } catch (error) {
    status.textContent = error.message;
  }
}

document.getElementById("google-calendar-connect")?.addEventListener("click", async () => {
  const button = document.getElementById("google-calendar-connect");
  const status = document.getElementById("google-calendar-status");

  if (button.textContent.includes("Disconnect")) {
    if (!confirm("Disconnect the Google account from My AI Agent? This disconnects both Calendar and Gmail.")) return;

    try {
      const response = await fetch("/api/google/calendar/disconnect", { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Disconnect failed.");
      await Promise.all([refreshGoogleCalendarStatus(), refreshGoogleGmailStatus()]);
    } catch (error) {
      status.textContent = error.message;
    }
    return;
  }

  window.location.href = "/api/google/connect";
});

document.getElementById("google-gmail-connect")?.addEventListener("click", () => {
  window.location.href = "/api/google/connect";
});

refreshGoogleCalendarStatus();
refreshGoogleGmailStatus();
