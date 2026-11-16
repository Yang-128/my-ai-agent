// Keep the local backend alive while at least one browser tab is open.
const browserTabId = sessionStorage.getItem("my-ai-agent-tab-id") || crypto.randomUUID();
sessionStorage.setItem("my-ai-agent-tab-id", browserTabId);

async function sendBrowserHeartbeat() {
  try {
    await fetch("/api/session/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: browserTabId })
    });
  } catch (_) {}
}

sendBrowserHeartbeat();
setInterval(sendBrowserHeartbeat, 2000);

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

let manualSelectionResolver = null;

toolPickerButton.addEventListener("click", async () => {
  toolPickerMenu.hidden = !toolPickerMenu.hidden;
  if (!toolPickerMenu.hidden) {
    await refreshComposerTools();
  }
});

toolPickerMenu.querySelector(".tool-picker-close").addEventListener("click", () => {
  toolPickerMenu.hidden = true;
  if (manualSelectionResolver) {
    const resolve = manualSelectionResolver;
    manualSelectionResolver = null;
    resolve(null);
  }
});

document.addEventListener("pointerdown", event => {
  if (manualSelectionResolver) return;
  if (!toolPickerMenu.hidden && !toolPickerMenu.contains(event.target)) {
    toolPickerMenu.hidden = true;
  }
}, true);

function updateToolPickerLabel() {
  toolPickerButton.textContent = selectedTools.size
    ? `Tools (${selectedTools.size}) ▾`
    : "Tools (off) ▾";
}

async function refreshComposerTools(allowDisabled = false, targetMenu = toolPickerMenu) {
  const list = targetMenu.querySelector(".tool-picker-list");

  try {
    const response = await fetch("/api/tools");
    if (!response.ok) throw new Error("Could not load tools");

    const tools = await response.json();
    selectedTools.clear();
    list.replaceChildren();

    if (!tools.length) {
      list.textContent = "No tools available.";
      updateToolPickerLabel();
      return;
    }

    [...tools]
      .sort((a, b) => Number(a.enabled === false) - Number(b.enabled === false))
      .forEach(tool => {
      const label = document.createElement("label");
      label.className = tool.enabled === false ? "tool-picker-option disabled" : "tool-picker-option";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = tool.enabled !== false;
      checkbox.disabled = tool.enabled === false && !allowDisabled;

      if (checkbox.checked) selectedTools.add(tool.name);

      const details = document.createElement("span");
      details.className = "tool-picker-details";

      const name = document.createElement("strong");
      name.textContent = tool.name;
      if (tool.enabled === false) {
        name.style.textDecoration = "line-through";
        label.style.opacity = "0.5";
      }

      const description = document.createElement("small");
      description.textContent = tool.enabled === false
        ? (allowDisabled ? "Currently disabled — select to enable" : "Disabled in Tools & MCP")
        : (tool.description || tool.source);

      details.append(name, description);
      label.append(checkbox, details);
      list.append(label);

      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          selectedTools.add(tool.name);
          label.classList.remove("disabled");
          label.style.opacity = "1";
          name.style.textDecoration = "none";
          description.textContent = tool.description || tool.source;
        } else {
          selectedTools.delete(tool.name);
          label.classList.add("disabled");
          label.style.opacity = "0.5";
          name.style.textDecoration = "line-through";
          description.textContent = allowDisabled
            ? "Will be disabled when you confirm"
            : "Disabled in Tools & MCP";
        }
        updateToolPickerLabel();
      });
    });

    updateToolPickerLabel();
  } catch (error) {
    list.textContent = error.message;
  }
}



async function chooseManualTools() {
  const overlay = document.createElement("div");
  overlay.className = "tool-selection-overlay";
  overlay.innerHTML = `
    <section class="tool-selection-dialog" role="dialog" aria-modal="true" aria-labelledby="tool-selection-title">
      <header class="tool-selection-heading">
        <h2 id="tool-selection-title">Choose tools</h2>
        <p>Select the tools this request may use.</p>
      </header>
      <div class="tool-picker-list">Loading tools...</div>
      <footer class="tool-selection-actions">
        <button type="button" class="tool-selection-cancel">Cancel</button>
        <button type="button" class="tool-selection-confirm">Confirm</button>
      </footer>
    </section>
  `;
  document.body.append(overlay);

  const list = overlay.querySelector(".tool-picker-list");
  const cancelButton = overlay.querySelector(".tool-selection-cancel");
  const confirmButton = overlay.querySelector(".tool-selection-confirm");

  await refreshComposerTools(true, overlay);

  return new Promise(resolve => {
    manualSelectionResolver = resolve;

    const close = result => {
      if (manualSelectionResolver !== resolve) return;
      manualSelectionResolver = null;
      overlay.remove();
      resolve(result);
    };

    cancelButton.addEventListener("click", () => close(null));

    confirmButton.addEventListener("click", async () => {
      if (manualSelectionResolver !== resolve) return;

      confirmButton.disabled = true;
      cancelButton.disabled = true;

      try {
        const selected = new Set(selectedTools);
        const response = await fetch("/api/tools");
        if (!response.ok) throw new Error("Could not load available tools.");

        const tools = await response.json();
        const updates = await Promise.all(tools.map(tool =>
          fetch(`/api/tools/${encodeURIComponent(tool.name)}/enabled`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: selected.has(tool.name) })
          })
        ));

        if (updates.some(item => !item.ok)) {
          throw new Error("Could not update the selected tools.");
        }

        await refreshComposerTools();
        close([...selected]);
      } catch (error) {
        confirmButton.disabled = false;
        cancelButton.disabled = false;
        let errorMessage = list.querySelector(".tool-selection-error");
        if (!errorMessage) {
          errorMessage = document.createElement("p");
          errorMessage.className = "tool-selection-error";
          list.append(errorMessage);
        }
        errorMessage.textContent = error.message;
      }
    });
  });
}

let currentConversationId = null;
let busy = false;
let selectedModel = "openai/gpt-oss-120b";
let selectedProvider = "groq";
let providerConfigs = {};

const providerSelector = document.createElement("select");
providerSelector.id = "provider-selector";
providerSelector.innerHTML = `
  <option value="groq">Groq</option>
  <option value="openrouter">OpenRouter</option>
  <option value="gemini">Google Gemini</option>
`;
form.parentElement.insertBefore(providerSelector, form);

const modelSearch = document.createElement("input");
modelSearch.id = "model-search";
modelSearch.type = "search";
modelSearch.placeholder = "Search available models...";
modelSearch.autocomplete = "off";
// Model search field removed.

const modelSelector = document.createElement("select");
modelSelector.id = "model-selector";

function getSelectedModelInfo() {
  const provider = providerConfigs[selectedProvider];
  return (provider?.models || []).find(model => model.id === selectedModel);
}

function getGeminiSpeedInfo(model) {
  const id = (model.id || "").toLowerCase();

  if (id.includes("flash-lite")) return { rank: 0, label: "Fastest" };
  if (id.includes("flash")) return { rank: 1, label: "Fast" };
  if (id.includes("gemma") && id.includes("26b")) {
    return { rank: 2, label: "Balanced" };
  }
  if (id.includes("gemma") || id.includes("pro")) {
    return { rank: 3, label: "Slower" };
  }
  return { rank: 4, label: "Speed varies" };
}

function populateModelSelector(filter = modelSearch.value) {
  const provider = providerConfigs[selectedProvider];
  const models = [...(provider?.models || [])];

  if (selectedProvider === "gemini") {
    models.sort((a, b) => {
      const difference =
        getGeminiSpeedInfo(a).rank - getGeminiSpeedInfo(b).rank;
      return difference || (a.name || a.id).localeCompare(b.name || b.id);
    });
  }
  const previousModel = selectedModel;
  const query = filter.trim().toLowerCase();

  const filtered = models.filter(model =>
    `${model.name || model.id}`.toLowerCase().includes(query)
  );

  modelSelector.innerHTML = "";

  filtered.forEach(model => {
    const option = document.createElement("option");
    option.value = model.id;
    const speedLabel = selectedProvider === "gemini"
      ? ` · ${getGeminiSpeedInfo(model).label}`
      : "";
    option.textContent =
      `${model.name || model.id}${speedLabel}${model.free ? " · FREE" : ""}`;
    modelSelector.appendChild(option);
  });

  if (!filtered.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = models.length ? "No matching models" : "No models loaded";
    modelSelector.appendChild(option);
  }

  const preferred = filtered.some(m => m.id === previousModel)
    ? previousModel
    : filtered.some(m => m.id === provider?.default_model)
      ? provider.default_model
      : filtered[0]?.id || "";

  modelSelector.value = preferred;
  if (preferred) selectedModel = preferred;
  updateModelDisplay();
}

modelSearch.addEventListener("input", () =>
  populateModelSelector(modelSearch.value)
);

form.parentElement.insertBefore(modelSelector, form);

const providerModelInput = document.createElement("input");
providerModelInput.id = "provider-model-input";
providerModelInput.placeholder = "Enter provider model ID";
providerModelInput.style.display = "none";
form.parentElement.insertBefore(providerModelInput, form);

const providerStatus = document.createElement("span");
providerStatus.id = "provider-status";
providerStatus.textContent = "Checking providers...";
form.parentElement.insertBefore(providerStatus, form);

function updateModelDisplay() {
  const provider = providerConfigs[selectedProvider];
  const providerName = provider?.name || selectedProvider;
  const model = getSelectedModelInfo()?.name || selectedModel || "No model selected";

  modelButton.textContent = `${providerName} · ${model}`;
  modelButton.title = `Active provider: ${providerName}\nModel: ${model}`;
}

function updateProviderControls() {
  modelSearch.value = "";
  populateModelSelector("");

  modelSelector.style.display = "";
  providerModelInput.style.display = "none";

  selectedModel = modelSelector.value;
  updateModelDisplay();
}

providerSelector.addEventListener("change", async () => {
  selectedProvider = providerSelector.value;
  updateProviderControls();
  await refreshProviderStatus();
});

modelSelector.addEventListener("change", () => {
  selectedModel = modelSelector.value;
  updateModelDisplay();
});

providerModelInput.addEventListener("input", () => {
  selectedModel = providerModelInput.value.trim();
  updateModelDisplay();
});

async function refreshProviderStatus() {
  try {
    const response = await fetch("/api/providers");
    if (!response.ok) throw new Error("Status unavailable");

    const providers = await response.json();

    providerConfigs = {};
    providers.forEach(provider => {
      providerConfigs[provider.id] = provider;
    });

    const current = providerConfigs[selectedProvider];

    providerStatus.textContent = current
      ? (current.configured ? "● API key configured" : "○ API key missing")
      : "Provider unavailable";

    providerStatus.className = current?.configured
      ? "provider-ready"
      : "provider-missing";

    updateProviderControls();

  } catch (error) {
    providerStatus.textContent = "Status unavailable";
    console.error(error);
  }
}

function scrollToBottom() {
  const area = document.getElementById("conversation");
  area.scrollTop = area.scrollHeight;
}

function renderMessageContent(message, text) {
  const markers = [
    { marker: "<!--CALENDAR_APPROVAL:", type: "calendar" },
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

    function formatDate(value, allDay = false) {
      if (!value) return "";
      if (allDay) {
        const parts = value.split("-").map(Number);
        const date = new Date(parts[0], parts[1] - 1, parts[2]);
        return Number.isNaN(date.getTime())
          ? value
          : date.toLocaleDateString([], { dateStyle: "full" });
      }
      const date = new Date(value);
      return Number.isNaN(date.getTime())
        ? value
        : date.toLocaleString([], { dateStyle: "full", timeStyle: "short" });
    }

    const proposalTimeInputs = [];

    function toDateTimeLocal(value) {
      if (!value) return "";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "";
      const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
      return local.toISOString().slice(0, 16);
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
        const allDay = Boolean(event.start?.date || event.end?.date);
        const startValue = event.start?.dateTime || event.start?.date || event.start_time;
        const endValue = event.end?.dateTime || event.end?.date || event.end_time;
        if (startValue) lines.push(`Start: ${formatDate(startValue, allDay)}`);
        if (endValue) lines.push(
          allDay
            ? `End date (exclusive): ${formatDate(endValue, true)}`
            : `End: ${formatDate(endValue)}`
        );
        if (event.recurrence?.length) lines.push(`Repeats: ${event.recurrence.join("; ")}`);
        if (event.location) lines.push(`Location: ${event.location}`);
        if (event.description) lines.push(`Description: ${event.description}`);
      } else if (action === "delete") {
        lines.push("This event will be permanently deleted from Google Calendar.");
        if (item.event_id) lines.push(`Event ID: ${item.event_id}`);
      }

      details.textContent = lines.join("\n");
      section.appendChild(details);

      if (action === "create" || action === "update") {
        const allDay = Boolean(event.start?.date || event.end?.date);
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
          startInput, endInput, startTime, endTime, allDayToggle, action
        });
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
      const timedFields = proposalTimeInputs.filter(
        fields => fields && fields.action !== "delete" && !fields.allDayToggle.checked
      );
      const hasInvalidMinute = timedFields.some(fields =>
        fields.startTime.hasInvalidMinute() || fields.endTime.hasInvalidMinute()
      );

      if (approved && hasInvalidMinute) {
        status.hidden = false;
        status.textContent = "Minute must be a whole number between 0 and 59.";
        return;
      }

      if (approved && proposalTimeInputs.some((fields, index) =>
        fields && fields.action !== "delete" && (
          !proposals[index]._calendarEditor?.titleInput.value.trim() ||
          !fields.startInput.value ||
          !fields.endInput.value ||
          (!fields.allDayToggle.checked &&
            (!fields.startTime.values() || !fields.endTime.values())) ||
          (fields.allDayToggle.checked &&
            fields.endInput.value <= fields.startInput.value)
        )
      )) {
        status.hidden = false;
        status.textContent = "Check that every event has a title, valid start and end dates, and complete times for timed events. All-day end dates must be after their start dates.";
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
                location: editor.locationInput?.value.trim() || ""
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

async function refreshHistory() {
  const response = await fetch("/api/conversations");
  if (!response.ok) throw new Error("Could not load conversation history");

  const conversations = await response.json();
  history.innerHTML = "";

  conversations.forEach(conversation => {
    const item = document.createElement("button");
    item.className = "history-item";
    item.textContent = conversation.title;
    item.title = conversation.title;

    if (conversation.id === currentConversationId) {
      item.classList.add("selected");
    }

    item.addEventListener("click", () => openConversation(conversation.id));
    history.appendChild(item);
  });
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

        if (decision.needs_extensions && decision.required_extensions.length) {
          const names = decision.required_extensions
            .map(name => name.replace(/^mcp_/, "").replaceAll("_", " "))
            .join("\n• ");

          const approved = window.confirm(
            "I recommend enabling these extensions for this request:\n\n" +
            "• " + names +
            (decision.reason ? "\n\nReason: " + decision.reason : "") +
            "\n\nApprove this selection?\n\n" +
            "Choose Cancel to select tools manually."
          );

          if (approved) {
            const toolsResponse = await fetch("/api/tools");
            if (!toolsResponse.ok) throw new Error("Could not load available extensions.");

            const availableTools = await toolsResponse.json();
            const mcpTools = availableTools.filter(tool => tool.source === "MCP");

            const updates = await Promise.all(mcpTools.map(tool =>
              fetch(`/api/tools/${encodeURIComponent(tool.name)}/enabled`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  enabled: decision.required_extensions.includes(tool.name)
                })
              })
            ));

            if (updates.some(response => !response.ok)) {
              throw new Error("Could not update extension settings.");
            }

            toolsForThisMessage = decision.required_extensions;
            await refreshComposerTools();
          } else {
            const manualTools = await chooseManualTools();
            if (manualTools === null) {
              responseMessage.textContent = "Request cancelled.";
              return;
            }
            toolsForThisMessage = manualTools;
          }
        } else {
          const disableAll = window.confirm(
            "No extensions are needed for this request.\n\n" +
            "Disable all MCP extensions?\n\n" +
            "Choose Cancel to select tools manually."
          );

          if (disableAll) {
            const toolsResponse = await fetch("/api/tools");
            if (!toolsResponse.ok) throw new Error("Could not load available extensions.");

            const availableTools = await toolsResponse.json();
            const mcpTools = availableTools.filter(tool => tool.source === "MCP");

            const updates = await Promise.all(mcpTools.map(tool =>
              fetch(`/api/tools/${encodeURIComponent(tool.name)}/enabled`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled: false })
              })
            ));

            if (updates.some(response => !response.ok)) {
              throw new Error("Could not disable extensions.");
            }

            toolsForThisMessage = [];
            await refreshComposerTools();
          } else {
            const manualTools = await chooseManualTools();
            if (manualTools === null) {
              responseMessage.textContent = "Request cancelled.";
              return;
            }
            toolsForThisMessage = manualTools;
          }
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

      if (missingToolMatch && retryWithMissingTool) {
        const missingTool = missingToolMatch[1];
        const approved = window.confirm(
          `The model tried to use an extension that was not included: ${missingTool}\n\n` +
          "Add this extension to the current request and retry?"
        );

        if (approved) {
          const toolsResponse = await fetch("/api/tools");
          if (!toolsResponse.ok) {
            throw new Error("Could not load available extensions.");
          }
          const availableTools = await toolsResponse.json();
          if (!availableTools.some(tool => tool.name === missingTool)) {
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

      renderMessageContent(responseMessage, responseRaw);
      if (!responseMessage.textContent.trim()) {
        responseMessage.textContent = "The model returned an empty response.";
      }
      break;
    }

    await refreshHistory();

  } catch (error) {
    responseMessage.textContent = "Error: " + error.message;
  } finally {
    input.disabled = false;
    input.focus();
    busy = false;
    scrollToBottom();
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

    selectedModel = providerConfigs.groq?.default_model || selectedModel;
    modelSelector.value = selectedModel;
    updateModelDisplay();

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
  breadcrumb.innerHTML = "Workspace <span>/</span> Usage";

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
  breadcrumb.innerHTML = "Workspace <span>/</span> Chat";

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
      const tpd = Number(item.tpd_used || 0) === 0
        ? "—"
        : quotaText(item.tpd_used, item.tpd_limit);

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

      const tr = document.createElement("tr");

      const modelStatus = hasKnownDailyLimit(item)
        ? (isDailyLimitReached(item) ? "Daily limit reached" : "Available")
        : "Limit unknown";

      [
        `${providerName} · ${modelName}`,
        modelStatus,
        rpm,
        tpm,
        rpd,
        tpd,
        average
      ].forEach(value => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });

      const resetCell = document.createElement("td");
      resetCell.className = "usage-reset";
      resetCell.dataset.duration = item.requests_reset || "";
      resetCell.dataset.updated = item.updated_at || "";
      tr.appendChild(resetCell);

      rows.appendChild(tr);
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
    const builtIn = tools.filter(t => t.source === "Built-in").length;
    const mcp = tools.filter(t => t.source === "MCP").length;

    summary.innerHTML = `
      <div class="usage-card"><span>Total available tools</span><strong>${tools.length}</strong></div>
      <div class="usage-card"><span>Built-in tools</span><strong>${builtIn}</strong></div>
      <div class="usage-card"><span>MCP server tools</span><strong>${mcp}</strong></div>
    `;
    list.innerHTML = "";

    if (!tools.length) {
      list.textContent = "No tools are currently available.";
      return;
    }

    tools.forEach(tool => {
      const card = document.createElement("article");
      card.className = "tool-card";

      const header = document.createElement("div");
      header.className = "tool-card-header";

      const name = document.createElement("strong");
      name.className = "tool-name";
      name.textContent = tool.name;

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
          toggleText.textContent = payload.enabled ? "Enabled" : "Disabled";
          runButton.disabled = !payload.enabled;
          runButton.textContent = payload.enabled ? "Run tool" : "Tool disabled";
          card.classList.toggle("tool-disabled", !payload.enabled);
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

      card.append(header, description, toggleLabel, parameterArea, runButton, result);
      list.appendChild(card);
    });
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
  breadcrumb.innerHTML = "Workspace <span>/</span> Tools & MCP";

  await refreshTools();
}



let currentFilesPath = "";
let currentFilesLocation = "workspace";

async function loadFiles(path = currentFilesPath) {
  const list = document.getElementById("files-list");
  const breadcrumb = document.getElementById("files-breadcrumb");
  const upButton = document.getElementById("files-up");
  const preview = document.getElementById("file-preview");
  const endpoint = currentFilesLocation === "documents" ? "/api/documents" : "/api/files";

  list.textContent = "Loading...";
  preview.innerHTML = '<div class="file-preview-empty">Select a file to preview its contents.</div>';

  try {
    const response = await fetch(`${endpoint}?path=${encodeURIComponent(path)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load files");

    currentFilesPath = data.path;
    const rootLabel = currentFilesLocation === "documents" ? "My AI Agent Documents" : "Workspace";
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
  const endpoint = currentFilesLocation === "documents" ? "/api/documents/content" : "/api/files/content";
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

  document.querySelector(".history-label").style.display = "none";
  breadcrumb.innerHTML = "Workspace <span>/</span> Files";

  await loadFiles("");
}

document.getElementById("files-location").addEventListener("change", event => {
  currentFilesLocation = event.target.value;
  currentFilesPath = "";
  document.getElementById("file-preview").innerHTML =
    '<div class="file-preview-empty">Select a file to preview its contents.</div>';
  loadFiles("");
});

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
