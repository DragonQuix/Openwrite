import { $, $$, api, showToast, state } from "/js/core.js";

let refreshWorkspace = async () => {};

const kindLabels = {
  character: "人物",
  world: "世界与组织",
  progression: "成长体系",
};

const characterFields = [
  ["name", "姓名", "input"],
  ["aliases", "别名", "textarea", "每行一个"],
  ["tier", "角色层级", "input"],
  ["summary", "概要", "textarea", "一句话定位"],
  ["personality", "性格", "textarea"],
  ["goal", "目标", "textarea"],
  ["fear", "恐惧", "textarea"],
  ["taboos", "禁忌", "textarea", "每行一个"],
  ["appearance", "外观", "textarea"],
  ["voice", "说话特征", "textarea"],
  ["current_state", "当前状态", "textarea"],
  ["state_updated_at", "状态更新时间", "input"],
  ["organization", "所属组织", "input"],
  ["progression_system", "成长体系 ID", "input"],
  ["progression_stage", "当前阶段 ID", "input"],
  ["tags", "标签", "textarea", "每行一个"],
  ["detail_refs", "详情引用", "textarea", "每行一个"],
  ["related", "关系引用", "textarea", "每行 target|kind|note"],
];

const worldFields = [
  ["name", "名称", "input"],
  ["kind", "实体类型", "select", ["organization", "faction", "place", "concept", "object", "event", "custom"]],
  ["status", "状态", "input"],
  ["summary", "概要", "textarea"],
  ["tags", "标签", "textarea", "每行一个"],
  ["detail_refs", "详情引用", "textarea", "每行一个"],
  ["related", "关系引用", "textarea", "每行 target|kind|note"],
];

export function bindAssetUI(callbacks = {}) {
  refreshWorkspace = callbacks.refreshWorkspace || refreshWorkspace;
  $("#asset-create").addEventListener("click", () => newAsset(state.assets.kind));
  $("#asset-package-import-open").addEventListener("click", openPackageDialog);
  $("#asset-package-export").addEventListener("click", exportSelected);
  $("#asset-package-close").addEventListener("click", closePackageDialog);
  $("#asset-package-cancel").addEventListener("click", closePackageDialog);
  $("#asset-package-back").addEventListener("click", resetPackagePicker);
  $("#asset-package-preview").addEventListener("click", previewPackage);
  $("#asset-package-apply").addEventListener("click", applyPackage);
  $("#asset-form").addEventListener("submit", saveAsset);
  $$("[data-asset-kind]").forEach((button) => {
    button.addEventListener("click", () => selectKind(button.dataset.assetKind));
  });
  $$("[data-asset-mode]").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.assetMode));
  });
}

export async function refreshAssets(kind = state.assets.kind) {
  if (!state.workspace?.initialized) return;
  state.assets.kind = kind;
  const result = await api(`/api/assets?kind=${encodeURIComponent(kind)}`);
  const data = result.data || result;
  state.assets.items = data.assets || [];
  renderAssetTabs();
  renderAssetList();
  if (state.assets.selected && state.assets.selected.kind === kind) {
    await loadAsset(state.assets.selected.id, false);
  } else {
    clearAssetEditor();
  }
}

function renderAssetTabs() {
  $$("[data-asset-kind]").forEach((button) => {
    button.classList.toggle("active", button.dataset.assetKind === state.assets.kind);
  });
}

function renderAssetList() {
  const root = $("#asset-list");
  root.replaceChildren();
  const items = state.assets.items;
  $("#asset-list-status").textContent = items.length
    ? `${items.length} 个${kindLabels[state.assets.kind]}，选择一项编辑`
    : `暂无${kindLabels[state.assets.kind]}，可以新建一个。`;
  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "asset-list-item";
    button.classList.toggle(
      "active",
      state.assets.selected?.kind === item.kind && state.assets.selected?.id === item.id,
    );
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(button.classList.contains("active")));
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.assets.selectedForExport.has(`${item.kind}:${item.id}`);
    checkbox.setAttribute(
      "aria-label",
      `选择导出 ${item.name || item.id} (${item.id})`,
    );
    checkbox.addEventListener("click", (event) => {
      event.stopPropagation();
      const key = `${item.kind}:${item.id}`;
      if (checkbox.checked) state.assets.selectedForExport.add(key);
      else state.assets.selectedForExport.delete(key);
    });
    const text = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = item.name || item.id;
    const summary = document.createElement("span");
    summary.textContent = item.summary ? `${item.id} · ${item.summary}` : item.id;
    text.append(name, summary);
    button.append(checkbox, text);
    button.addEventListener("click", () => loadAsset(item.id, true));
    root.append(button);
  });
}

function selectKind(kind) {
  if (!kind || kind === state.assets.kind) return;
  state.assets.selected = null;
  state.assets.selectedForExport = new Set();
  refreshAssets(kind).catch((error) => showToast(error.message, true));
}

async function loadAsset(id, pushState) {
  try {
    const result = await api(`/api/assets/${encodeURIComponent(state.assets.kind)}/${encodeURIComponent(id)}`);
    state.assets.selected = { kind: state.assets.kind, id };
    state.assets.draft = { ...(result.data || result), isNew: false };
    state.assets.mode = "structured";
    renderAssetList();
    renderAssetEditor();
    if (pushState) history.replaceState({}, "", "#assets");
  } catch (error) {
    showToast(error.message, true);
  }
}

function newAsset(kind) {
  state.assets.kind = kind;
  state.assets.selected = null;
  state.assets.draft = {
    kind,
    id: "",
    name: "",
    data: kind === "progression"
      ? { name: "", kind: "ability", summary: "", stages: [{ id: "stage_001", name: "", requirements: [] }] }
      : {},
    body_markdown: "",
    raw_text: "",
    revision: "",
    isNew: true,
  };
  state.assets.mode = "structured";
  $("#asset-form-status").textContent = "";
  renderAssetTabs();
  renderAssetList();
  renderAssetEditor();
}

function clearAssetEditor() {
  state.assets.draft = null;
  $("#asset-empty").hidden = false;
  $("#asset-form").hidden = true;
}

function renderAssetEditor() {
  const draft = state.assets.draft;
  if (!draft) return clearAssetEditor();
  $("#asset-empty").hidden = true;
  $("#asset-form").hidden = false;
  $("#asset-editor-kind").textContent = kindLabels[draft.kind] || draft.kind;
  $("#asset-editor-title").textContent = draft.isNew ? "新建资产" : (draft.name || draft.id);
  $("#asset-editor-path").textContent = draft.path || "尚未保存";
  $$("[data-asset-mode]").forEach((button) => button.classList.toggle("active", button.dataset.assetMode === state.assets.mode));
  $("#asset-structured-fields").hidden = state.assets.mode !== "structured";
  $("#asset-raw-field").hidden = state.assets.mode !== "raw";
  $("#asset-raw-text").value = draft.raw_text || "";
  renderStructuredFields(draft);
}

function renderStructuredFields(draft) {
  const root = $("#asset-structured-fields");
  root.replaceChildren();
  const id = addField(root, "id", "资产 ID", draft.id, "input", "只能使用字母、数字、下划线、点或短横线");
  id.input.readOnly = !draft.isNew;
  id.wrapper.classList.add("full-span");
  if (draft.kind === "progression") {
    addField(root, "name", "体系名称", draft.data?.name || "", "input");
    addChoiceField(root, "kind", "体系类型", draft.data?.kind || "ability", ["ability", "rank", "cultivation", "career", "reputation", "curse", "custom"]);
    addField(root, "summary", "概要", draft.data?.summary || "", "textarea", "", true);
    renderStages(root, draft.data?.stages || []);
    addField(root, "body_markdown", "说明（可选 Markdown）", draft.body_markdown || "", "textarea", "", true);
    return;
  }
  const fields = draft.kind === "character" ? characterFields : worldFields;
  fields.forEach(([key, label, type, hint]) => {
    if (type === "select") {
      addChoiceField(root, key, label, draft.data?.[key] || "organization", hint);
      return;
    }
    const value = serializeField(key, draft.data?.[key]);
    addField(root, key, label, value, type, hint, ["summary", "personality", "goal", "fear", "taboos", "appearance", "voice", "current_state", "related", "detail_refs"].includes(key));
  });
  addField(root, "body_markdown", "自由 Markdown 详情", draft.body_markdown || "", "textarea", "", true);
}

function addField(root, key, label, value, type = "input", hint = "", fullSpan = false) {
  const wrapper = document.createElement("label");
  wrapper.className = `asset-field${fullSpan ? " full-span" : ""}`;
  const title = document.createElement("span");
  title.textContent = label;
  const input = document.createElement(type === "textarea" ? "textarea" : "input");
  input.dataset.assetField = key;
  input.value = value || "";
  if (type === "textarea") input.rows = key === "body_markdown" ? 10 : 3;
  wrapper.append(title, input);
  if (hint) {
    const help = document.createElement("small");
    help.textContent = hint;
    wrapper.append(help);
  }
  root.append(wrapper);
  return { wrapper, input };
}

function addChoiceField(root, key, label, value, choices) {
  const wrapper = document.createElement("label");
  wrapper.className = "asset-field";
  const title = document.createElement("span");
  title.textContent = label;
  const select = document.createElement("select");
  select.dataset.assetField = key;
  choices.forEach((choice) => {
    const option = document.createElement("option");
    option.value = choice;
    option.textContent = choice;
    option.selected = choice === value;
    select.append(option);
  });
  wrapper.append(title, select);
  root.append(wrapper);
}

function renderStages(root, stages) {
  const wrapper = document.createElement("div");
  wrapper.className = "asset-field full-span";
  const heading = document.createElement("div");
  heading.className = "subsection-heading";
  const title = document.createElement("span");
  title.textContent = "阶段";
  const add = document.createElement("button");
  add.type = "button";
  add.className = "text-button";
  add.textContent = "新增阶段";
  add.addEventListener("click", () => {
    const list = wrapper.querySelector(".asset-stage-list");
    list.append(stageRow({ id: `stage_${String(list.children.length + 1).padStart(3, "0")}`, name: "", requirements: [] }));
  });
  heading.append(title, add);
  const list = document.createElement("div");
  list.className = "asset-stage-list";
  (stages.length ? stages : [{ id: "stage_001", name: "", requirements: [] }]).forEach((stage) => list.append(stageRow(stage)));
  wrapper.append(heading, list);
  root.append(wrapper);
}

function stageRow(stage) {
  const row = document.createElement("div");
  row.className = "asset-stage-row";
  ["id", "name"].forEach((key) => {
    const input = document.createElement("input");
    input.dataset.stageField = key;
    input.value = stage[key] || "";
    input.placeholder = key === "id" ? "stage_id" : "阶段名称";
    row.append(input);
  });
  const requirements = document.createElement("textarea");
  requirements.dataset.stageField = "requirements";
  requirements.rows = 2;
  requirements.placeholder = "每行一个前置条件";
  requirements.value = (stage.requirements || []).join("\n");
  row.append(requirements);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon-button";
  remove.setAttribute("aria-label", "删除阶段");
  remove.title = "删除阶段";
  remove.textContent = "×";
  remove.addEventListener("click", () => row.remove());
  row.append(remove);
  return row;
}

function setMode(mode) {
  if (mode === "raw" && state.assets.draft?.isNew) {
    showToast("请先用字段模式创建资产，再切换原文", true);
    return;
  }
  state.assets.mode = mode === "raw" ? "raw" : "structured";
  renderAssetEditor();
}

async function saveAsset(event) {
  event.preventDefault();
  const draft = state.assets.draft;
  if (!draft) return;
  const button = $("#asset-save");
  button.disabled = true;
  $("#asset-form-status").textContent = "保存中…";
  try {
    const payload = {
      kind: draft.kind,
      id: draft.isNew ? collectField("id").trim() : draft.id,
      revision: draft.revision,
    };
    if (state.assets.mode === "raw") {
      payload.raw_text = $("#asset-raw-text").value;
    } else {
      payload.data = collectData(draft.kind);
      payload.body_markdown = collectField("body_markdown");
    }
    const result = await api(draft.isNew ? "/api/assets" : "/api/assets/update", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const saved = result.data?.asset || result.asset;
    state.assets.selected = { kind: saved.kind, id: saved.id };
    state.assets.draft = { ...saved, isNew: false };
    $("#asset-form-status").textContent = "已保存并同步运行态";
    showToast("资产已保存");
    await refreshAssets(draft.kind);
    await refreshWorkspace();
  } catch (error) {
    $("#asset-form-status").textContent = error.code === "ASSET_CONFLICT"
      ? "资产已变化，请重新载入后再保存"
      : error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function collectData(kind) {
  if (kind === "progression") {
    const data = {};
    $$("[data-asset-field]").forEach((input) => {
      if (input.dataset.assetField === "body_markdown") return;
      data[input.dataset.assetField] = input.value;
    });
    data.stages = $$(".asset-stage-row").map((row) => {
      const stage = {};
      row.querySelectorAll("[data-stage-field]").forEach((input) => {
        stage[input.dataset.stageField] = input.dataset.stageField === "requirements"
          ? splitLines(input.value)
          : input.value.trim();
      });
      return stage;
    });
    return data;
  }
  const data = {};
  $$("[data-asset-field]").forEach((input) => {
    const key = input.dataset.assetField;
    if (key === "body_markdown") return;
    data[key] = ["aliases", "taboos", "tags", "detail_refs"].includes(key)
      ? splitLines(input.value)
      : key === "related" ? parseRelations(input.value) : input.value.trim();
  });
  return data;
}

function collectField(key) {
  return $("[data-asset-field=\"" + key + "\"]")?.value || "";
}

function serializeField(key, value) {
  if (["aliases", "taboos", "tags", "detail_refs"].includes(key)) return (value || []).join("\n");
  if (key === "related") return (value || []).map((item) => [item.target, item.kind, item.note].filter(Boolean).join("|" )).join("\n");
  return value == null ? "" : String(value);
}

function splitLines(value) {
  return String(value || "").split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
}

function parseRelations(value) {
  return splitLines(value).map((line) => {
    const [target, kind, note] = line.split("|").map((item) => item.trim());
    return { target, kind: kind || "related", ...(note ? { note } : {}) };
  }).filter((item) => item.target);
}

function openPackageDialog() {
  resetPackagePicker();
  $("#asset-package-dialog").showModal();
}

function closePackageDialog() {
  $("#asset-package-dialog").close();
}

function resetPackagePicker() {
  state.assets.packagePreview = null;
  $("#asset-package-picker").hidden = false;
  $("#asset-package-preview-panel").hidden = true;
  $("#asset-package-file").value = "";
  $("#asset-package-status").textContent = "";
}

async function previewPackage() {
  const file = $("#asset-package-file").files[0];
  if (!file) {
    $("#asset-package-status").textContent = "请选择 .owasset.zip 文件";
    return;
  }
  const button = $("#asset-package-preview");
  button.disabled = true;
  $("#asset-package-status").textContent = "正在校验资产包…";
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 8192) binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
    const result = await api("/api/assets/package/preview", {
      method: "POST",
      body: JSON.stringify({ package_base64: btoa(binary), file_name: file.name }),
    });
    state.assets.packagePreview = result.data || result;
    renderPackagePreview();
  } catch (error) {
    $("#asset-package-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderPackagePreview() {
  const preview = state.assets.packagePreview;
  $("#asset-package-picker").hidden = true;
  $("#asset-package-preview-panel").hidden = false;
  $("#asset-package-counts").textContent = `${preview.counts.new} 个新增 · ${preview.counts.conflict} 个冲突`;
  $("#asset-package-source").textContent = preview.source_novel ? `来自 ${preview.source_novel}` : "可读资产包";
  const root = $("#asset-package-assets");
  root.replaceChildren();
  (preview.assets || []).forEach((item) => {
    const row = document.createElement("div");
    row.className = `asset-package-item ${item.status}`;
    row.dataset.assetId = item.id;
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${kindLabels[item.kind] || item.kind} · ${item.name || item.id}`;
    const status = document.createElement("span");
    status.textContent = item.status === "conflict" ? "已有同 ID 资产" : "新增资产";
    copy.append(title, status);
    if (item.diff) {
      const diff = document.createElement("pre");
      diff.textContent = item.diff;
      copy.append(diff);
    }
    const controls = document.createElement("div");
    const select = document.createElement("select");
    select.dataset.packageAction = item.id;
    [item.status === "conflict" ? "skip" : "import", ...(item.status === "conflict" ? ["replace", "rename"] : [])].forEach((action) => {
      const option = document.createElement("option");
      option.value = action;
      option.textContent = action === "skip" ? "跳过" : action === "replace" ? "替换" : action === "rename" ? "重命名" : "导入";
      select.append(option);
    });
    controls.append(select);
    if (item.status === "conflict") {
      const rename = document.createElement("input");
      rename.dataset.packageRename = item.id;
      rename.placeholder = "重命名 ID";
      rename.hidden = true;
      controls.append(rename);
      select.addEventListener("change", () => { rename.hidden = select.value !== "rename"; });
    }
    row.append(copy, controls);
    root.append(row);
  });
  const missing = preview.missing_dependencies || [];
  $("#asset-package-missing-row").hidden = !missing.length;
  $("#asset-package-missing").textContent = missing.join("、");
  $("#asset-package-apply").disabled = false;
  $("#asset-package-status").textContent = "预览完成。确认每个冲突的处理方式后再导入。";
}

async function applyPackage() {
  const preview = state.assets.packagePreview;
  if (!preview) return;
  const resolutions = {};
  $$("[data-package-action]").forEach((select) => {
    const id = select.dataset.packageAction;
    const resolution = { action: select.value };
    if (select.value === "rename") resolution.new_id = $(`[data-package-rename=\"${id}\"]`).value.trim();
    resolutions[id] = resolution;
  });
  const button = $("#asset-package-apply");
  button.disabled = true;
  $("#asset-package-status").textContent = "正在原子导入并同步…";
  try {
    await api("/api/assets/package/import", {
      method: "POST",
      body: JSON.stringify({
        upload_id: preview.upload_id,
        package_sha256: preview.package_sha256,
        resolutions,
        allow_missing_dependencies: $("#asset-package-allow-missing").checked,
      }),
    });
    closePackageDialog();
    await refreshAssets(state.assets.kind);
    await refreshWorkspace();
    showToast("资产包已导入");
  } catch (error) {
    $("#asset-package-status").textContent = error.message;
    button.disabled = false;
  }
}

function exportSelected() {
  const selected = Array.from(state.assets.selectedForExport);
  const query = selected.map((value) => `select=${encodeURIComponent(value)}`).join("&");
  window.location.href = `/api/assets/package/export${query ? `?${query}` : ""}`;
}
