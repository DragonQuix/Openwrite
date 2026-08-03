import { $, $$, api, formatNumber, modelPresets, showToast, state } from "/js/core.js";

const routeLabels = {
  goethe: "Goethe 规划",
  dante: "Dante 正文",
  chapter_write: "章节写作",
  review: "章节审稿",
  source_extract: "来源提取",
  revision: "修订生成",
};

let selectedProfileId = "";

function surface() {
  return state.workspace?.model_profiles || { profiles: [], routes: {}, default_profile_id: "default" };
}

function operationForCurrentView() {
  if (state.view === "agents") return state.agent;
  return {
    chapters: "chapter_write",
    review: "review",
    tools: "source_extract",
  }[state.view] || "goethe";
}

function profileForOperation(operation) {
  const models = surface();
  const profileId = models.routes?.[operation] || models.default_profile_id;
  return models.profiles.find((profile) => profile.id === profileId) || null;
}

export function updateRoutedModelIndicator() {
  const operation = operationForCurrentView();
  const profile = profileForOperation(operation);
  const label = profile ? `${routeLabels[operation]} · ${profile.label}` : "未配置";
  $("#model-topbar-name").textContent = label;
  $("#model-connection-dot").classList.toggle("ready", Boolean(profile?.configured));
  const button = $("#model-settings-open");
  button.title = profile
    ? `${routeLabels[operation]}使用 ${profile.model}`
    : "打开模型设置";
  const writer = profileForOperation("chapter_write");
  $("#write-open").disabled = !writer?.configured;
  $("#write-open").title = writer?.configured ? "" : "请先配置章节写作路由的模型档案";
}

export function renderModelProfilesUI() {
  const models = surface();
  if (!models.profiles.some((profile) => profile.id === selectedProfileId)) {
    selectedProfileId = models.default_profile_id || models.profiles[0]?.id || "";
  }
  renderProfileList();
  renderRouteGrid();
  updateRoutedModelIndicator();
  if ($("#model-dialog").open) fillProfileForm(selectedProfile());
}

function selectedProfile() {
  return surface().profiles.find((profile) => profile.id === selectedProfileId) || null;
}

function renderProfileList() {
  const root = $("#model-profile-list");
  root.replaceChildren();
  const models = surface();
  models.profiles.forEach((profile) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "model-profile-item";
    button.classList.toggle("active", profile.id === selectedProfileId);
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(profile.id === selectedProfileId));
    const name = document.createElement("strong");
    name.textContent = profile.label;
    const meta = document.createElement("span");
    meta.textContent = `${profile.model} · ${profile.configured ? "已配置" : "缺少 Key"}`;
    button.append(name, meta);
    button.addEventListener("click", () => {
      selectedProfileId = profile.id;
      renderProfileList();
      fillProfileForm(profile);
    });
    root.append(button);
  });
  if (!models.profiles.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "尚无模型档案";
    root.append(empty);
  }
}

function emptyProfile() {
  return {
    id: "",
    label: "",
    provider: "openai",
    base_url: modelPresets.openai.base_url,
    model: modelPresets.openai.model,
    api_format: "chat",
    context_tokens: 64000,
    max_output_tokens: 24000,
    temperature: 0.7,
    timeout_seconds: 120,
    configured: false,
  };
}

function detectPreset(profile) {
  const base = String(profile?.base_url || "").toLowerCase();
  const name = String(profile?.model || "").toLowerCase();
  if (base.includes("deepseek.com")) {
    return name === "deepseek-v4-flash" ? "deepseek-flash" : "deepseek-pro";
  }
  if (profile?.provider === "anthropic") return "anthropic";
  if (base.includes("api.openai.com")) return "openai";
  return "custom";
}

function fillProfileForm(value) {
  const profile = value || emptyProfile();
  $("#model-profile-id").value = profile.id || "";
  $("#model-profile-id").readOnly = Boolean(profile.id);
  $("#model-profile-label").value = profile.label || "";
  $("#model-preset").value = detectPreset(profile);
  $("#model-base-url").value = profile.base_url || "";
  $("#model-name").value = profile.model || "";
  $("#model-api-format").value = profile.api_format || "chat";
  $("#model-context-tokens").value = String(profile.context_tokens || 64000);
  $("#model-max-tokens").value = String(profile.max_output_tokens || 24000);
  $("#model-temperature").value = String(profile.temperature ?? 0.7);
  $("#model-timeout").value = String(profile.timeout_seconds || 120);
  $("#model-api-key").value = "";
  $("#model-key-state").textContent = profile.configured
    ? "本机已有凭据；留空即可沿用"
    : "此档案尚未保存 Key";
  $("#model-dialog-current").textContent = profile.id
    ? `${profile.label} · ${formatNumber(profile.context_tokens)} 上下文`
    : "新建模型档案";
  $("#model-dialog-status-dot").classList.toggle("ready", Boolean(profile.configured));
  renderDeleteFallback(profile.id || "");
}

function renderDeleteFallback(profileId) {
  const select = $("#model-delete-fallback");
  select.replaceChildren();
  surface().profiles.filter((profile) => profile.id !== profileId).forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = `回退到 ${profile.label}`;
    select.append(option);
  });
  $("#model-profile-delete").disabled = !profileId || surface().profiles.length <= 1;
  select.hidden = !profileId || surface().profiles.length <= 1;
}

function profilePayload() {
  const preset = modelPresets[$("#model-preset").value] || modelPresets.custom;
  const existing = selectedProfile();
  return {
    id: $("#model-profile-id").value.trim(),
    label: $("#model-profile-label").value.trim(),
    provider: preset.provider,
    base_url: $("#model-base-url").value.trim(),
    model: $("#model-name").value.trim(),
    api_key: $("#model-api-key").value.trim(),
    api_format: $("#model-api-format").value,
    context_tokens: Number($("#model-context-tokens").value),
    max_output_tokens: Number($("#model-max-tokens").value),
    temperature: Number($("#model-temperature").value),
    timeout_seconds: Number($("#model-timeout").value),
    credential_ref: existing?.credential_ref || "",
    remember_api_key: $("#model-remember-key").checked,
  };
}

async function saveProfile(event) {
  event.preventDefault();
  const submit = $("#model-submit");
  submit.disabled = true;
  $("#model-progress").textContent = "正在保存模型档案…";
  try {
    const result = await api("/api/model/profiles", {
      method: "POST",
      body: JSON.stringify(profilePayload()),
    });
    state.workspace.model_profiles = result.model_profiles;
    selectedProfileId = result.profile.id;
    renderModelProfilesUI();
    $("#model-progress").textContent = "档案已保存，任务路由立即生效。";
    showToast(`已保存模型档案 ${result.profile.label}`);
  } catch (error) {
    $("#model-progress").textContent = error.message;
  } finally {
    submit.disabled = false;
  }
}

async function testConnection() {
  const button = $("#model-test");
  button.disabled = true;
  $("#model-progress").textContent = "正在发送最小连接测试…";
  try {
    const result = await api("/api/model/test", {
      method: "POST",
      body: JSON.stringify(profilePayload()),
    });
    $("#model-progress").textContent = `连接成功 · ${result.model} · ${formatNumber(result.latency_ms)} ms`;
  } catch (error) {
    $("#model-progress").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function deleteProfile() {
  const profile = selectedProfile();
  if (!profile) return;
  if (!window.confirm(`删除模型档案“${profile.label}”？`)) return;
  try {
    const result = await api("/api/model/profiles/delete", {
      method: "POST",
      body: JSON.stringify({
        profile_id: profile.id,
        fallback_id: $("#model-delete-fallback").value,
      }),
    });
    state.workspace.model_profiles = result.model_profiles;
    selectedProfileId = result.model_profiles.default_profile_id;
    renderModelProfilesUI();
    showToast("模型档案已删除");
  } catch (error) {
    $("#model-progress").textContent = error.message;
  }
}

function renderRouteGrid() {
  const root = $("#model-route-grid");
  root.replaceChildren();
  const models = surface();
  Object.entries(routeLabels).forEach(([route, label]) => {
    const field = document.createElement("label");
    field.className = "model-route-field";
    const title = document.createElement("span");
    title.textContent = label;
    const select = document.createElement("select");
    select.dataset.modelRoute = route;
    models.profiles.forEach((profile) => {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = `${profile.label} · ${profile.model}`;
      select.append(option);
    });
    select.value = models.routes?.[route] || models.default_profile_id;
    field.append(title, select);
    root.append(field);
  });
}

async function saveRoutes() {
  const routes = {};
  $$('[data-model-route]').forEach((select) => { routes[select.dataset.modelRoute] = select.value; });
  try {
    const result = await api("/api/model/routes", {
      method: "POST",
      body: JSON.stringify({ routes }),
    });
    state.workspace.model_profiles = result.model_profiles;
    renderModelProfilesUI();
    $("#model-progress").textContent = "任务路由已保存，下一次操作立即生效。";
    showToast("任务路由已更新");
  } catch (error) {
    $("#model-progress").textContent = error.message;
  }
}

function switchTab(tab) {
  $$('[data-model-tab]').forEach((button) => {
    const active = button.dataset.modelTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $("#model-profiles-pane").hidden = tab !== "profiles";
  $("#model-routes-pane").hidden = tab !== "routes";
}

function applyPreset() {
  const preset = modelPresets[$("#model-preset").value];
  if (!preset) return;
  $("#model-base-url").value = preset.base_url;
  $("#model-name").value = preset.model;
  $("#model-api-format").value = preset.api_format;
  $("#model-context-tokens").value = String(preset.context_tokens);
  $("#model-max-tokens").value = String(preset.max_tokens);
}

function toggleKey() {
  const input = $("#model-api-key");
  const visible = input.type === "text";
  input.type = visible ? "password" : "text";
  $("#model-key-toggle").textContent = visible ? "显示" : "隐藏";
  $("#model-key-toggle").setAttribute("aria-pressed", String(!visible));
}

export function openModelProfilesDialog(tab = "profiles") {
  renderModelProfilesUI();
  fillProfileForm(selectedProfile());
  switchTab(tab);
  $("#model-progress").textContent = "";
  $("#model-dialog").showModal();
}

export function bindModelProfilesUI() {
  $("#model-form").addEventListener("submit", saveProfile);
  $("#model-profile-new").addEventListener("click", () => {
    selectedProfileId = "";
    renderProfileList();
    fillProfileForm(null);
    $("#model-profile-id").focus();
  });
  $("#model-profile-delete").addEventListener("click", deleteProfile);
  $("#model-route-save").addEventListener("click", saveRoutes);
  $("#model-test").addEventListener("click", testConnection);
  $("#model-key-toggle").addEventListener("click", toggleKey);
  $("#model-preset").addEventListener("change", applyPreset);
  $$('[data-model-tab]').forEach((button) => button.addEventListener("click", () => switchTab(button.dataset.modelTab)));
  $("#model-close").addEventListener("click", () => $("#model-dialog").close());
  $("#model-cancel").addEventListener("click", () => $("#model-dialog").close());
}
