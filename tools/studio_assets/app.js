"use strict";

const state = {
  workspace: null,
  view: "dashboard",
  document: null,
  dirty: false,
  saving: false,
  agent: "goethe",
  continuity: null,
  outline: null,
  outlineSelectedId: null,
  relationship: {
    nodes: [], edges: [], positions: new Map(), selectedId: null,
    scale: 1, tx: 0, ty: 0, paused: false, frame: null, ticks: 0, pointer: null,
  },
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const labels = {
  outline: "可写大纲",
  story: "故事资产",
  characters: "人物档案",
  world: "世界设定",
  chapters: "正文章节",
};
const readinessLabels = {
  author_intent: "作者意图",
  background: "故事背景",
  foundation: "基础设定",
  characters: "主要人物",
  outline: "可写大纲",
  creative_focus: "创作罗盘",
};

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-OpenWrite-Studio"] = "1";
  }
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("Content-Type") || "";
  const body = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(body?.error || `请求失败 (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function countWritingUnits(text) {
  const withoutHeadings = text.replace(/^\s{0,3}#{1,6}\s+.*$/gm, "");
  const cjk = withoutHeadings.match(/[\u3400-\u4dbf\u4e00-\u9fff]/g) || [];
  const words = withoutHeadings
    .replace(/[\u3400-\u4dbf\u4e00-\u9fff]/g, " ")
    .match(/[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*/g) || [];
  return cjk.length + words.length;
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2600);
}

function setSaveState(message, dirty = false) {
  $("#save-state").textContent = message;
  $("#save-document").disabled = !dirty || state.saving;
}

async function loadWorkspace() {
  state.workspace = await api("/api/workspace");
  renderWorkspace();
  renderRecentProjects();
  document.querySelector("#app").setAttribute("aria-busy", "false");
  if (!state.workspace.initialized && !$("#project-dialog").open) {
    $("#project-dialog").showModal();
  }
}

function renderWorkspace() {
  const { snapshot, model } = state.workspace;
  $("#book-title").textContent = snapshot.title;
  $("#book-location").textContent = `${snapshot.current_arc} / ${snapshot.current_chapter}`;
  $("#metric-words").textContent = formatNumber(snapshot.writing_units);
  $("#metric-chapters").textContent = formatNumber(snapshot.chapters);
  $("#metric-characters").textContent = formatNumber(snapshot.characters);
  $("#metric-hooks").textContent = formatNumber(snapshot.pending_foreshadowing);

  const percent = snapshot.target_units
    ? Math.min(100, Math.round((snapshot.writing_units / snapshot.target_units) * 100))
    : 0;
  $("#progress-percent").textContent = `${percent}%`;
  $("#progress-current").textContent = `${formatNumber(snapshot.writing_units)} 字`;
  $("#progress-target").textContent = snapshot.target_units
    ? `目标 ${formatNumber(snapshot.target_units)} 字`
    : "目标未设置";
  $("#progress-fill").style.width = `${percent}%`;
  const progress = $(".progress-track");
  progress.setAttribute("aria-valuenow", String(percent));

  const modelState = $("#model-state");
  modelState.textContent = model.configured ? model.name : "模型未配置";
  modelState.classList.toggle("ready", model.configured);
  if (model.configured && !$("#model-name").value) $("#model-name").value = model.name;
  $("#write-open").disabled = !model.configured;
  $("#write-open").title = model.configured ? "" : "请通过环境变量配置 LLM_API_KEY";

  renderReadiness(snapshot.readiness);
  renderRecentChapters();
  renderNextActions(snapshot.next_actions);
  fillFocus(snapshot.creative_focus);
  $("#fact-arc").textContent = snapshot.current_arc;
  $("#fact-chapter").textContent = snapshot.current_chapter;
  $("#fact-stage").textContent = snapshot.stage;
  $("#fact-world").textContent = String(snapshot.world_documents);
  $("#fact-tokens").textContent = formatNumber(snapshot.total_tokens);
  $("#fact-review-score").textContent = snapshot.reviewed_chapters
    ? `${snapshot.average_review_score} / 100`
    : "-";
  renderDocumentList(state.view === "dashboard" ? "chapters" : state.view);
  renderOperations();
}

function renderReadiness(readiness) {
  const root = $("#readiness-list");
  root.replaceChildren();
  let readyCount = 0;
  Object.entries(readinessLabels).forEach(([key, label]) => {
    const ready = Boolean(readiness[key]);
    if (ready) readyCount += 1;
    const row = document.createElement("div");
    row.className = `readiness-row${ready ? " ready" : ""}`;
    const name = document.createElement("span");
    name.textContent = label;
    const status = document.createElement("span");
    status.className = "readiness-state";
    status.textContent = ready ? "就绪" : "待完善";
    row.append(name, status);
    root.append(row);
  });
  $("#readiness-score").textContent = `${readyCount} / ${Object.keys(readinessLabels).length}`;
}

function renderRecentChapters() {
  const root = $("#recent-chapters");
  root.replaceChildren();
  const chapters = state.workspace.documents.chapters.slice(-5).reverse();
  if (!chapters.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "尚无正文";
    root.append(empty);
    return;
  }
  chapters.forEach((doc) => {
    const button = document.createElement("button");
    button.className = "recent-row";
    button.type = "button";
    const title = document.createElement("span");
    title.textContent = doc.title;
    const meta = document.createElement("span");
    meta.textContent = doc.subtitle;
    button.append(title, meta);
    button.addEventListener("click", () => openDocument(doc.path, true));
    root.append(button);
  });
}

function renderNextActions(actions) {
  const root = $("#next-actions");
  root.replaceChildren();
  actions.forEach((action) => {
    const span = document.createElement("span");
    span.className = "next-action";
    span.textContent = action;
    root.append(span);
  });
}

function fillFocus(focus) {
  $("#focus-goal").value = focus.goal || "";
  $("#focus-keep").value = (focus.must_keep || []).join("\n");
  $("#focus-avoid").value = (focus.must_avoid || []).join("\n");
  $("#focus-notes").value = (focus.notes || []).join("\n");
}

function renderDocumentList(group) {
  const root = $("#document-list");
  root.replaceChildren();
  const documents = state.workspace?.documents[group] || [];
  $("#document-group-title").textContent = labels[group] || "最近章节";
  $("#document-count").textContent = String(documents.length);
  if (!documents.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = group === "chapters" ? "尚无正文" : "暂无文档";
    root.append(empty);
    return;
  }
  documents.forEach((doc) => {
    const button = document.createElement("button");
    button.className = "document-item";
    button.classList.toggle("active", state.document?.path === doc.path);
    button.type = "button";
    button.setAttribute("role", "listitem");
    const title = document.createElement("strong");
    title.textContent = doc.title;
    const subtitle = document.createElement("span");
    subtitle.textContent = doc.subtitle;
    button.append(title, subtitle);
    button.addEventListener("click", () => openDocument(doc.path, true));
    root.append(button);
  });
}

function setView(view, pushHistory = true) {
  state.view = view;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  const dashboard = view === "dashboard";
  const outlineView = view === "outline";
  const documentView = ["chapters", "story", "characters", "world"].includes(view);
  $("#dashboard-view").hidden = !dashboard;
  $("#editor-view").hidden = !documentView;
  $("#outline-view").hidden = !outlineView;
  $("#search-view").hidden = view !== "search";
  $("#agents-view").hidden = view !== "agents";
  $("#continuity-view").hidden = view !== "continuity";
  $("#tools-view").hidden = view !== "tools";
  renderDocumentList(outlineView ? "outline" : (dashboard || !documentView ? "chapters" : view));
  if (documentView && (!state.document || documentGroup(state.document.path) !== view)) {
    const first = state.workspace.documents[view]?.[0];
    if (first) openDocument(first.path, false);
  }
  if (outlineView) loadOutline();
  if (view === "continuity") loadContinuity();
  if (pushHistory) {
    history.pushState({ view }, "", dashboard ? "/" : `/#${encodeURIComponent(view)}`);
  }
}

async function openDocument(path, pushHistory) {
  if (state.dirty && !window.confirm("当前文档尚未保存，仍要离开吗？")) return;
  try {
    const doc = await api(`/api/document?path=${encodeURIComponent(path)}`);
    state.document = doc;
    state.dirty = false;
    const group = documentGroup(path);
    state.view = group;
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === group));
    $("#dashboard-view").hidden = true;
    $("#editor-view").hidden = false;
    $("#outline-view").hidden = true;
    $("#search-view").hidden = true;
    $("#agents-view").hidden = true;
    $("#continuity-view").hidden = true;
    $("#tools-view").hidden = true;
    $("#editor-path").textContent = doc.path;
    $("#editor-title").value = doc.title;
    $("#document-editor").value = doc.content;
    $("#review-document").hidden = group !== "chapters" || !state.workspace.model.configured;
    $("#outline-tree-back").hidden = group !== "outline";
    updateEditorCount();
    setSaveState("已保存", false);
    renderDocumentList(group);
    if (pushHistory) {
      history.pushState({ path }, "", `/#doc=${encodeURIComponent(path)}`);
    }
    $("#document-editor").focus();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadOutline(chapterId = "") {
  try {
    const suffix = chapterId ? `?chapter=${encodeURIComponent(chapterId)}` : "";
    state.outline = await api(`/api/outline${suffix}`);
    if (chapterId) state.outlineSelectedId = chapterId;
    if (!state.outlineSelectedId) state.outlineSelectedId = state.outline.recommendation?.chapter_id || state.outline.roots[0]?.id || null;
    renderOutline();
  } catch (error) {
    showToast(error.message, true);
  }
}

function flattenOutline(nodes, result = []) {
  nodes.forEach((node) => {
    result.push(node);
    flattenOutline(node.children || [], result);
  });
  return result;
}

function renderOutline() {
  const outline = state.outline;
  if (!outline) return;
  const counts = outline.counts || {};
  $("#outline-stats").textContent = `${counts.volume || 0} 卷 · ${counts.act || 0} 幕 · ${counts.section || 0} 节 · ${counts.chapter || 0} 章 · ${outline.drafted_chapters || 0} 章已有正文`;
  $("#outline-tree-count").textContent = String((counts.volume || 0) + (counts.act || 0) + (counts.section || 0) + (counts.chapter || 0));
  const root = $("#outline-tree");
  root.replaceChildren();
  (outline.roots || []).forEach((node) => root.append(buildOutlineTreeItem(node)));
  renderOutlineDetail();
  const smart = $("#outline-smart-create");
  smart.disabled = !outline.recommendation || !state.workspace.model.configured;
  smart.title = state.workspace.model.configured ? "" : "请先配置模型";
}

function buildOutlineTreeItem(node) {
  const item = document.createElement("li");
  item.className = `outline-tree-item kind-${node.kind}`;
  item.setAttribute("role", "treeitem");
  item.dataset.nodeId = node.id;
  const row = document.createElement("div");
  row.className = "outline-tree-row";
  const children = node.children || [];
  const group = document.createElement("ul");
  group.setAttribute("role", "group");
  const expanded = node.kind !== "section" || (state.outline?.recommendation?.breadcrumb || []).includes(node.title);
  if (children.length) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "outline-tree-toggle";
    toggle.setAttribute("aria-label", `${expanded ? "收起" : "展开"}${node.title}`);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.textContent = expanded ? "−" : "+";
    group.hidden = !expanded;
    toggle.addEventListener("click", () => {
      const next = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(next));
      toggle.setAttribute("aria-label", `${next ? "收起" : "展开"}${node.title}`);
      toggle.textContent = next ? "−" : "+";
      group.hidden = !next;
    });
    row.append(toggle);
  } else {
    const spacer = document.createElement("span"); spacer.className = "outline-tree-spacer"; row.append(spacer);
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "outline-tree-node";
  button.setAttribute("aria-current", String(node.id === state.outlineSelectedId));
  const badge = document.createElement("span"); badge.className = "outline-kind-badge"; badge.textContent = node.label;
  const title = document.createElement("span"); title.className = "outline-tree-title"; title.textContent = node.title;
  button.append(badge, title);
  if (node.kind === "chapter") {
    const status = document.createElement("span"); status.className = `outline-chapter-status ${node.status}`; status.textContent = node.status === "drafted" ? "已写" : "待写"; button.append(status);
  }
  button.addEventListener("click", async () => {
    state.outlineSelectedId = node.id;
    if (node.kind === "chapter") await loadOutline(node.id); else renderOutline();
  });
  row.append(button);
  if (node.editable) {
    const actions = document.createElement("span");
    actions.className = "outline-row-actions";
    if (node.child_kind) {
      const add = document.createElement("button");
      add.type = "button";
      add.className = "outline-row-action";
      add.textContent = "+";
      add.setAttribute("aria-label", `在${node.title}下新增${outlineKindLabel(node.child_kind)}`);
      add.title = `新增${outlineKindLabel(node.child_kind)}`;
      add.addEventListener("click", () => openOutlineEditDialog("add_child", node));
      actions.append(add);
    }
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "outline-row-action";
    rename.textContent = "改";
    rename.setAttribute("aria-label", `修改${node.title}`);
    rename.title = "改名";
    rename.addEventListener("click", () => openOutlineEditDialog("rename", node));
    actions.append(rename);
    row.append(actions);
  }
  item.append(row);
  children.forEach((child) => group.append(buildOutlineTreeItem(child)));
  if (children.length) item.append(group);
  return item;
}

function renderOutlineDetail() {
  const nodes = flattenOutline(state.outline?.roots || []);
  const node = nodes.find((item) => item.id === state.outlineSelectedId);
  $("#outline-detail-title").textContent = node?.title || "选择一个节点";
  $("#outline-breadcrumb").textContent = node ? [...node.path, node.title].join(" / ") : "从左侧结构树查看卷、幕、节或章。";
  $("#outline-node-meta").textContent = node ? `${node.label} · 原文第 ${node.line} 行${node.kind === "chapter" ? ` · ${node.status === "drafted" ? "已有正文" : "尚未写作"}` : ""}` : "";
  $("#outline-node-summary").textContent = node?.summary || "这个节点尚未填写摘要。";
  $("#outline-node-source").disabled = !node;
  $("#outline-node-rename").disabled = !node?.editable;
  $("#outline-node-add-child").disabled = !node?.child_kind;
  $("#outline-node-add-child").textContent = node?.child_kind ? `新增${outlineKindLabel(node.child_kind)}` : "新增下级";
  $("#outline-node-add-after").disabled = !node?.editable;
  $("#outline-node-add-after").textContent = node?.editable ? `新增同级${node.label}` : "新增同级";
  $("#outline-node-delete").disabled = !node?.can_delete;
  $("#outline-node-delete").title = node?.editable && !node?.can_delete ? (node.delete_blocked_reason || "该节点不能安全删除") : "";
  const create = $("#outline-node-create");
  create.disabled = !node || node.kind !== "chapter" || (node.status !== "drafted" && !state.workspace.model.configured);
  create.textContent = node?.status === "drafted" ? "打开已写正文" : "用此章创建正文";
}

function selectedOutlineNode() {
  return flattenOutline(state.outline?.roots || []).find((node) => node.id === state.outlineSelectedId);
}

function outlineKindLabel(kind) {
  return { volume: "卷", act: "幕", section: "节", chapter: "章" }[kind] || "节点";
}

function suggestedOutlineTitle(kind) {
  const nodes = flattenOutline(state.outline?.roots || []);
  if (kind === "chapter") {
    const numbers = nodes
      .filter((node) => node.kind === "chapter")
      .map((node) => Number(String(node.id).match(/\d+/)?.[0] || 0));
    return `第${Math.max(0, ...numbers) + 1}章：新章节`;
  }
  const count = nodes.filter((node) => node.kind === kind).length + 1;
  return `第${count}${outlineKindLabel(kind)}：新${outlineKindLabel(kind)}`;
}

function openOutlineEditDialog(operation, node = null) {
  const dialog = $("#outline-edit-dialog");
  const titleField = $("#outline-edit-title-field");
  const submit = $("#outline-edit-submit");
  let kind = node?.kind || "volume";
  let heading = "编辑大纲节点";
  let context = "";
  let value = node?.title || "";
  let help = "修改会增量写回 src/outline.md，并自动建立 Git 存档。";
  const impact = $("#outline-edit-impact");
  impact.replaceChildren();
  impact.hidden = true;
  if (operation === "add_child") {
    kind = node?.child_kind || "volume";
    heading = `新增${outlineKindLabel(kind)}`;
    context = node ? `添加到“${node.title}”下面。` : "添加到大纲根节点。";
    value = suggestedOutlineTitle(kind);
  } else if (operation === "add_after") {
    heading = `新增同级${outlineKindLabel(kind)}`;
    context = `添加在“${node?.title || "当前节点"}”之后。`;
    value = suggestedOutlineTitle(kind);
  } else if (operation === "rename") {
    heading = `修改${node?.label || "节点"}标题`;
    context = `只修改“${node?.title || ""}”这一行，不重写其他大纲内容。`;
    if (node?.kind === "chapter" && node?.status === "drafted") {
      help = "该章已有正文：可以修改标题文字，但不能更换章节编号。";
    }
  } else if (operation === "delete") {
    heading = `删除${node?.label || "节点"}`;
    const count = node?.delete_renumber_count || 0;
    context = `将删除“${node?.title || ""}”及其 ${node?.descendant_count || 0} 个下级节点，并让 ${count} 个后续节点连续补位。`;
    const impactTitle = document.createElement("strong");
    impactTitle.textContent = count ? `编号影响 · ${count} 项` : "编号影响 · 无后续补位";
    impact.append(impactTitle);
    const preview = node?.delete_renumber_preview || [];
    if (preview.length) {
      const list = document.createElement("ul");
      preview.forEach((change) => {
        const item = document.createElement("li");
        item.textContent = `${change.old_title} → ${change.new_title}`;
        list.append(item);
      });
      impact.append(list);
      if (count > preview.length) {
        const rest = document.createElement("span");
        rest.textContent = `另有 ${count - preview.length} 项将在同一次保存中补位。`;
        impact.append(rest);
      }
    }
    if (node?.delete_renumber_skipped) {
      const skipped = document.createElement("span");
      skipped.textContent = `${node.delete_renumber_skipped} 个无编号标题将保持不变。`;
      impact.append(skipped);
    }
    impact.hidden = false;
    help = "删除、连续重编号与 Git 存档会原子完成；若影响已有正文的章节号，系统会阻止操作。";
  }
  $("#outline-edit-operation").value = operation;
  $("#outline-edit-node-id").value = node?.id || "";
  $("#outline-edit-kind").value = kind;
  $("#outline-edit-title").textContent = heading;
  $("#outline-edit-context").textContent = context;
  $("#outline-edit-name").value = value;
  $("#outline-edit-help").textContent = help;
  $("#outline-edit-progress").textContent = "";
  titleField.hidden = operation === "delete";
  $("#outline-edit-help").hidden = false;
  $("#outline-edit-name").required = operation !== "delete";
  submit.textContent = operation === "delete" ? "确认删除" : "保存修改";
  submit.className = operation === "delete" ? "danger-button" : "primary-button";
  submit.disabled = false;
  dialog.showModal();
  if (operation !== "delete") $("#outline-edit-name").select();
}

async function submitOutlineEdit(event) {
  event.preventDefault();
  const submit = $("#outline-edit-submit");
  const progress = $("#outline-edit-progress");
  submit.disabled = true;
  let blockedByConflict = false;
  progress.textContent = "正在安全写入大纲…";
  try {
    const payload = await api("/api/outline/edit", {
      method: "POST",
      body: JSON.stringify({
        operation: $("#outline-edit-operation").value,
        node_id: $("#outline-edit-node-id").value,
        kind: $("#outline-edit-kind").value,
        title: $("#outline-edit-name").value,
        revision: state.outline?.revision || "",
      }),
    });
    state.outline = payload.outline;
    state.outlineSelectedId = payload.selected_node_id || state.outline.recommendation?.chapter_id || null;
    renderOutline();
    $("#outline-edit-dialog").close();
    showToast(payload.message || "大纲已更新");
    await loadWorkspace();
  } catch (error) {
    progress.textContent = error.message;
    if (error.status === 409) {
      await loadOutline();
      progress.textContent = "大纲已刷新。请关闭窗口并重新选择节点，避免把旧操作应用到新结构。";
      blockedByConflict = true;
    }
  } finally {
    submit.disabled = blockedByConflict;
  }
}

function openOutlineSource(line = 1) {
  openDocument("src/outline.md", true).then(() => {
    const editor = $("#document-editor");
    const lines = editor.value.split("\n");
    editor.selectionStart = lines.slice(0, Math.max(0, line - 1)).join("\n").length;
    editor.selectionEnd = editor.selectionStart;
  });
}

async function openSmartWriteDialog(chapterId = "") {
  if (!state.outline || chapterId) await loadOutline(chapterId);
  const recommendation = state.outline?.recommendation;
  if (!recommendation) { showToast("大纲里没有可创建的章纲", true); return; }
  if (recommendation.status === "drafted") {
    const doc = state.workspace.documents.chapters.find((item) => item.path.endsWith(`/${recommendation.chapter_id}.md`));
    if (doc) await openDocument(doc.path, true); else showToast("章节正文记录不存在", true);
    return;
  }
  if (!state.workspace.model.configured) { showToast("请先配置模型", true); return; }
  $("#write-chapter-id").textContent = recommendation.chapter_id;
  $("#write-chapter-title").textContent = recommendation.title;
  $("#write-breadcrumb").textContent = recommendation.breadcrumb.join(" / ");
  $("#write-outline-revision").value = state.outline.revision;
  $("#write-guidance").value = recommendation.guidance;
  $("#write-words").value = String(recommendation.target_words);
  $("#write-dialog").showModal();
}

function documentGroup(path) {
  if (path === "src/outline.md") return "outline";
  if (path.startsWith("data/manuscript/")) return "chapters";
  if (path.startsWith("src/characters/")) return "characters";
  if (path.startsWith("src/world/")) return "world";
  return "story";
}

function updateEditorCount() {
  const count = countWritingUnits($("#document-editor").value);
  $("#editor-word-count").textContent = `${formatNumber(count)} 字`;
}

async function saveDocument() {
  if (!state.document || !state.dirty || state.saving) return;
  state.saving = true;
  setSaveState("保存中", true);
  try {
    const saved = await api("/api/document", {
      method: "PUT",
      body: JSON.stringify({
        path: state.document.path,
        content: $("#document-editor").value,
        version: state.document.version,
      }),
    });
    state.document = saved;
    state.dirty = false;
    setSaveState("已保存", false);
    showToast("文档已保存");
    await loadWorkspace();
  } catch (error) {
    setSaveState("保存失败", true);
    showToast(error.message, true);
  } finally {
    state.saving = false;
    $("#save-document").disabled = !state.dirty;
  }
}

async function saveFocus(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    state.workspace = await api("/api/focus", {
      method: "POST",
      body: JSON.stringify({
        goal: $("#focus-goal").value,
        must_keep: $("#focus-keep").value.split("\n"),
        must_avoid: $("#focus-avoid").value.split("\n"),
        notes: $("#focus-notes").value.split("\n"),
      }),
    });
    renderWorkspace();
    showToast("创作罗盘已更新");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function saveModel(event) {
  event.preventDefault();
  const submit = $("#model-submit");
  submit.disabled = true;
  $("#model-progress").textContent = "正在应用模型配置…";
  try {
    state.workspace = await api("/api/model", {
      method: "POST",
      body: JSON.stringify({
        provider: $("#model-provider").value,
        base_url: $("#model-base-url").value.trim(),
        model: $("#model-name").value.trim(),
        api_key: $("#model-api-key").value.trim(),
        api_format: $("#model-api-format").value,
      }),
    });
    $("#model-api-key").value = "";
    renderWorkspace();
    $("#model-dialog").close();
    showToast(`模型已切换为 ${state.workspace.model.name}`);
  } catch (error) {
    $("#model-progress").textContent = error.message;
  } finally {
    submit.disabled = false;
  }
}

async function initializeProject(event) {
  event.preventDefault();
  const submit = $("#project-submit");
  submit.disabled = true;
  $("#project-progress").textContent = "正在创建小说目录、真源和运行态…";
  try {
    state.workspace = await api("/api/project/init", {
      method: "POST",
      body: JSON.stringify({
        project_path: $("#project-path").value.trim(),
        novel_id: $("#project-id").value.trim(),
        title: $("#project-title").value.trim(),
      }),
    });
    renderWorkspace();
    $("#project-dialog").close();
    showToast("小说工作区已创建");
  } catch (error) {
    $("#project-progress").textContent = error.message;
  } finally {
    submit.disabled = false;
  }
}

async function openProject(event) {
  event.preventDefault();
  const submit = $("#open-project-submit");
  submit.disabled = true;
  $("#open-project-progress").textContent = "正在校验并打开作品…";
  try {
    state.workspace = await api("/api/project/open", {
      method: "POST",
      body: JSON.stringify({ project_path: $("#open-project-path").value.trim() }),
    });
    state.document = null;
    state.dirty = false;
    renderWorkspace();
    renderRecentProjects();
    $("#project-dialog").close();
    setView("dashboard");
    showToast(`已打开 ${state.workspace.snapshot.title}`);
  } catch (error) {
    $("#open-project-progress").textContent = error.message;
  } finally {
    submit.disabled = false;
  }
}

function renderRecentProjects() {
  const root = $("#recent-projects");
  root.replaceChildren();
  const projects = state.workspace?.project?.recent || [];
  projects.forEach((project) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "recent-project";
    const title = document.createElement("strong");
    title.textContent = project.title;
    const path = document.createElement("span");
    path.textContent = project.path;
    button.append(title, path);
    button.addEventListener("click", () => {
      $("#open-project-path").value = project.path;
    });
    root.append(button);
  });
}

async function searchProject(event) {
  event.preventDefault();
  const query = $("#search-query").value.trim();
  const scope = $("#search-scope").value;
  if (!query) return;
  $("#search-status").textContent = "正在更新本地索引并搜索…";
  const root = $("#search-results");
  root.replaceChildren();
  try {
    const payload = await api(`/api/search?q=${encodeURIComponent(query)}&scope=${encodeURIComponent(scope)}`);
    $("#search-status").textContent = `已索引 ${payload.indexed} 份文档，找到 ${payload.results.length} 条结果`;
    payload.results.forEach((result) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "search-result";
      const heading = document.createElement("strong");
      heading.textContent = result.heading || result.title;
      const location = document.createElement("span");
      location.textContent = `${result.path}:${result.line}`;
      const snippet = document.createElement("p");
      snippet.textContent = result.snippet;
      button.append(heading, location, snippet);
      button.addEventListener("click", () => openDocument(result.path, true));
      root.append(button);
    });
    if (!payload.results.length) root.textContent = "没有命中。可以缩短关键词或切换到“全部资产”。";
  } catch (error) {
    $("#search-status").textContent = error.message;
    showToast(error.message, true);
  }
}

function renderOperations() {
  const operations = state.workspace?.operations || {};
  const diagnostics = operations.diagnostics || [];
  const diagnosticRoot = $("#diagnostic-list");
  diagnosticRoot.replaceChildren();
  diagnostics.forEach((item) => {
    const row = document.createElement("div");
    row.className = `operation-row${item.ok ? " ok" : ""}`;
    const name = document.createElement("strong");
    name.textContent = item.name;
    const detail = document.createElement("span");
    detail.textContent = item.detail;
    row.append(name, detail);
    diagnosticRoot.append(row);
  });
  const sync = operations.sync || {};
  $("#sync-status").textContent = sync.needs_sync
    ? `待同步：大纲 ${sync.outline_pending ? "有变更" : "已同步"}，角色卡 ${sync.cards || 0}/${sync.profiles || 0}`
    : "src 与 data 已同步";
  renderSourcePacks(operations.source_packs || []);
}

function renderSourcePacks(packs) {
  const root = $("#source-list");
  root.replaceChildren();
  $("#source-count").textContent = String(packs.length);
  if (!packs.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "尚无来源包";
    root.append(empty);
    return;
  }
  packs.forEach((pack) => {
    const row = document.createElement("article");
    row.className = "source-row";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = pack.source_id;
    const meta = document.createElement("span");
    meta.textContent = [pack.style_ready ? "风格" : "", pack.setting_ready ? "设定" : ""]
      .filter(Boolean).join(" + ") || "提取中";
    copy.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "row-actions";
    [["审阅", "review"], ["全部晋升", "promote"], ["合成风格", "synthesize"]].forEach(([label, action]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "text-button";
      button.textContent = label;
      button.addEventListener("click", () => runSourceAction(action, pack.source_id));
      actions.append(button);
    });
    row.append(copy, actions);
    root.append(row);
  });
}

async function runSync() {
  const button = $("#sync-project");
  button.disabled = true;
  $("#sync-status").textContent = "正在同步大纲与角色卡…";
  try {
    const payload = await api("/api/sync", { method: "POST", body: "{}" });
    state.workspace = payload.workspace;
    renderWorkspace();
    showToast("src 与 data 已同步");
  } catch (error) {
    $("#sync-status").textContent = error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function inspectContext(event) {
  event.preventDefault();
  const chapter = $("#context-chapter").value.trim() || "next";
  $("#context-meta").textContent = "正在组装上下文…";
  try {
    const payload = await api(`/api/context?chapter=${encodeURIComponent(chapter)}`);
    const manifest = payload.manifest || {};
    $("#context-meta").textContent = `${payload.chapter_id} · 目标 ${formatNumber(payload.target_words)} 字 · ${payload.characters.length} 位相关人物 · 上下文 ${formatNumber(manifest.estimated_tokens)} tokens · revision ${manifest.revision || "-"}`;
    const provenance = (manifest.items || []).map((item) => {
      const sources = (item.sources || []).map((source) => source.path).join(", ");
      return `L${item.level} ${item.section} · ${item.estimated_tokens} tokens · ${sources}`;
    }).join("\n");
    $("#context-preview").textContent = `${provenance ? `【上下文来源】\n${provenance}\n\n` : ""}${payload.markdown || "上下文为空"}`;
  } catch (error) {
    $("#context-meta").textContent = error.message;
    showToast(error.message, true);
  }
}

async function importText(event) {
  event.preventDefault();
  const file = $("#import-file").files[0];
  if (!file) return;
  const button = event.currentTarget.querySelector("button[type=submit]");
  button.disabled = true;
  $("#import-status").textContent = "正在解析并导入章节…";
  try {
    const payload = await api("/api/import", {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        content: await file.text(),
        arc_id: $("#import-arc").value.trim(),
        start_number: $("#import-start").value,
        force: $("#import-force").checked,
      }),
    });
    state.workspace = payload.workspace;
    renderWorkspace();
    $("#import-status").textContent = `已导入 ${payload.imported.length} 章`;
    showToast(`已导入 ${payload.imported.length} 章正文`);
  } catch (error) {
    $("#import-status").textContent = error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function createDocument(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const payload = await api("/api/document/create", {
      method: "POST",
      body: JSON.stringify({
        kind: $("#create-kind").value,
        name: $("#create-name").value,
        description: $("#create-description").value,
      }),
    });
    state.workspace = payload.workspace;
    renderWorkspace();
    $("#create-status").textContent = "文档已创建";
    form.reset();
    await openDocument(payload.document.path, true);
  } catch (error) {
    $("#create-status").textContent = error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function submitChat(event) {
  event.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;
  appendChatMessage("user", "你", message);
  input.value = "";
  $("#chat-submit").disabled = true;
  $("#chat-status").textContent = `${state.agent === "goethe" ? "Goethe" : "Dante"} 正在读取项目并思考…`;
  try {
    const payload = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ agent: state.agent, message }),
    });
    appendChatMessage("assistant", state.agent === "goethe" ? "Goethe" : "Dante", payload.content || "本轮已执行完成。");
    state.workspace = payload.workspace;
    renderWorkspace();
  } catch (error) {
    appendChatMessage("assistant error", "系统", error.message);
  } finally {
    $("#chat-submit").disabled = false;
    $("#chat-status").textContent = "";
    input.focus();
  }
}

function appendChatMessage(role, author, content) {
  const item = document.createElement("article");
  item.className = `chat-message ${role}`;
  const name = document.createElement("strong");
  name.textContent = author;
  const body = document.createElement("p");
  body.textContent = content;
  item.append(name, body);
  $("#chat-log").append(item);
  item.scrollIntoView({ block: "end", behavior: "smooth" });
}

function chooseAgent(agent) {
  state.agent = agent;
  $$('[data-agent]').forEach((button) => button.classList.toggle("active", button.dataset.agent === agent));
  $("#chat-submit").textContent = `发送给 ${agent === "goethe" ? "Goethe" : "Dante"}`;
  $("#chat-input").placeholder = agent === "goethe"
    ? "例如：检查现有资产，帮我把第一篇推进到可写状态。"
    : "例如：写下一章，控制在 3000 字，保持当前创作罗盘。";
}

async function loadContinuity() {
  $("#truth-current").textContent = "载入中…";
  try {
    state.continuity = await api("/api/continuity");
    renderContinuity();
  } catch (error) {
    $("#truth-current").textContent = error.message;
    showToast(error.message, true);
  }
}

function renderContinuity() {
  const data = state.continuity || {};
  const truth = data.truth || {};
  $("#truth-current").textContent = truth.current_state || "尚无状态";
  $("#truth-ledger").textContent = truth.ledger || "尚无账本";
  $("#truth-relationships").textContent = truth.relationships || "尚无关系记录";
  renderRelationshipGraph(data.relationship_graph || {});
  const nodes = data.foreshadowing?.nodes || [];
  $("#foreshadow-count").textContent = String(nodes.length);
  const hookRoot = $("#foreshadow-list");
  hookRoot.replaceChildren();
  nodes.forEach((node) => {
    const row = document.createElement("div");
    row.className = "operation-row stacked";
    const heading = document.createElement("strong");
    heading.textContent = `${node.id} · 权重 ${node.weight}`;
    const content = document.createElement("span");
    content.textContent = node.content;
    row.append(heading, content);
    hookRoot.append(row);
  });
  if (!nodes.length) hookRoot.textContent = "暂无待处理伏笔";
  const workflows = data.workflows || [];
  $("#workflow-count").textContent = String(workflows.length);
  const workflowRoot = $("#workflow-list");
  workflowRoot.replaceChildren();
  workflows.forEach((workflow) => {
    const row = document.createElement("div");
    row.className = `operation-row stacked${workflow.error ? " error" : ""}`;
    const heading = document.createElement("strong");
    heading.textContent = `${workflow.chapter_id} · ${workflow.current_stage}`;
    const stages = document.createElement("span");
    stages.textContent = workflow.stages.map((stage) => `${stage.name}:${stage.status}`).join(" · ");
    row.append(heading, stages);
    workflowRoot.append(row);
  });
  if (!workflows.length) workflowRoot.textContent = "暂无活动 workflow";
}

const relationshipKinds = {
  character: "人物", faction: "势力", place: "地点", concept: "概念", unknown: "其他",
};

function renderRelationshipGraph(graph) {
  stopRelationshipSimulation();
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  const filter = $("#relationship-filter").value;
  const visibleNodes = filter === "all" ? nodes : nodes.filter((node) => node.kind === filter);
  const ids = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  state.relationship.nodes = visibleNodes;
  state.relationship.edges = visibleEdges;
  if (!ids.has(state.relationship.selectedId)) state.relationship.selectedId = null;

  const totalNodes = Number(graph.totals?.nodes ?? nodes.length);
  const totalEdges = Number(graph.totals?.edges ?? edges.length);
  const relationHint = totalNodes && !totalEdges
    ? " · 暂无结构化连线，请在实体中添加 related / 关联"
    : "";
  $("#relationship-summary").textContent = `${totalNodes} 个节点 · ${totalEdges} 条关系${graph.truncated ? " · 已按性能上限截取" : ""}${relationHint}`;
  $("#relationship-visible-count").textContent = String(visibleNodes.length);
  $("#relationship-empty").textContent = filter === "all"
    ? "暂无实体节点。请先在人物或世界实体目录中建立 Markdown 真源。"
    : "当前类型筛选下没有节点。";
  $("#relationship-empty").hidden = visibleNodes.length > 0;

  initializeRelationshipPositions(visibleNodes);
  buildRelationshipSvg(visibleNodes, visibleEdges);
  renderRelationshipNodeList();
  renderRelationshipDetail();
  applyRelationshipTransform();
  updateRelationshipPositions();

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  state.relationship.paused = reduceMotion;
  updateRelationshipPauseButton();
  if (!reduceMotion && visibleNodes.length > 1) startRelationshipSimulation();
}

function initializeRelationshipPositions(nodes, reset = false) {
  const count = Math.max(nodes.length, 1);
  nodes.forEach((node, index) => {
    if (!reset && state.relationship.positions.has(node.id)) return;
    const hash = Array.from(node.id).reduce((value, char) => ((value * 31) + char.charCodeAt(0)) >>> 0, 7);
    const angle = (index / count) * Math.PI * 2 + (hash % 17) / 40;
    const ring = 145 + (hash % 3) * 55;
    state.relationship.positions.set(node.id, {
      x: 480 + Math.cos(angle) * ring, y: 280 + Math.sin(angle) * ring, vx: 0, vy: 0, fixed: false,
    });
  });
}

function buildRelationshipSvg(nodes, edges) {
  const edgeRoot = $("#relationship-edges");
  const nodeRoot = $("#relationship-nodes");
  edgeRoot.replaceChildren();
  nodeRoot.replaceChildren();
  edges.forEach((edge) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.classList.add("relationship-edge");
    line.dataset.edgeId = edge.id;
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = edge.label;
    line.append(title);
    edgeRoot.append(line);
  });
  nodes.forEach((node) => {
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.classList.add("relationship-node");
    group.dataset.nodeId = node.id;
    group.dataset.kind = node.kind;
    group.setAttribute("tabindex", "0");
    group.setAttribute("role", "button");
    group.setAttribute("aria-label", `${node.label}，${relationshipKinds[node.kind] || "其他"}节点`);
    const shape = relationshipNodeShape(node.kind);
    shape.classList.add("relationship-node-shape");
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.classList.add("relationship-node-label");
    label.setAttribute("y", "34");
    label.textContent = node.label.length > 12 ? `${node.label.slice(0, 11)}…` : node.label;
    group.append(shape, label);
    group.addEventListener("pointerdown", beginRelationshipNodeDrag);
    group.addEventListener("keydown", moveRelationshipNodeWithKeyboard);
    group.addEventListener("click", () => selectRelationshipNode(node.id));
    nodeRoot.append(group);
  });
}

function relationshipNodeShape(kind) {
  const ns = "http://www.w3.org/2000/svg";
  if (kind === "faction") {
    const shape = document.createElementNS(ns, "rect");
    shape.setAttribute("x", "-18"); shape.setAttribute("y", "-18");
    shape.setAttribute("width", "36"); shape.setAttribute("height", "36"); shape.setAttribute("rx", "5");
    return shape;
  }
  if (kind === "place" || kind === "concept") {
    const shape = document.createElementNS(ns, "polygon");
    shape.setAttribute("points", kind === "place" ? "0,-22 22,0 0,22 -22,0" : "-20,-12 0,-23 20,-12 20,12 0,23 -20,12");
    return shape;
  }
  const shape = document.createElementNS(ns, "circle");
  shape.setAttribute("r", kind === "character" ? "19" : "17");
  return shape;
}

function updateRelationshipPositions() {
  const byId = state.relationship.positions;
  $$(".relationship-edge").forEach((line, index) => {
    const edge = state.relationship.edges[index];
    const source = byId.get(edge?.source);
    const target = byId.get(edge?.target);
    if (!source || !target) return;
    line.setAttribute("x1", String(source.x)); line.setAttribute("y1", String(source.y));
    line.setAttribute("x2", String(target.x)); line.setAttribute("y2", String(target.y));
  });
  $$(".relationship-node").forEach((group) => {
    const point = byId.get(group.dataset.nodeId);
    if (point) group.setAttribute("transform", `translate(${point.x} ${point.y})`);
  });
}

function startRelationshipSimulation() {
  if (state.relationship.frame || state.relationship.paused) return;
  state.relationship.ticks = 0;
  const tick = () => {
    state.relationship.frame = null;
    if (state.relationship.paused) return;
    simulateRelationshipStep();
    updateRelationshipPositions();
    state.relationship.ticks += 1;
    if (state.relationship.ticks < 360) state.relationship.frame = requestAnimationFrame(tick);
  };
  state.relationship.frame = requestAnimationFrame(tick);
}

function stopRelationshipSimulation() {
  if (state.relationship.frame) cancelAnimationFrame(state.relationship.frame);
  state.relationship.frame = null;
}

function simulateRelationshipStep() {
  const nodes = state.relationship.nodes;
  const positions = state.relationship.positions;
  for (let i = 0; i < nodes.length; i += 1) {
    const a = positions.get(nodes[i].id);
    if (!a || a.fixed) continue;
    for (let j = i + 1; j < nodes.length; j += 1) {
      const b = positions.get(nodes[j].id);
      if (!b) continue;
      let dx = a.x - b.x; let dy = a.y - b.y;
      const distance2 = Math.max(dx * dx + dy * dy, 100);
      const distance = Math.sqrt(distance2);
      const force = Math.min(1.5, 1500 / distance2);
      dx /= distance; dy /= distance;
      a.vx += dx * force; a.vy += dy * force;
      if (!b.fixed) { b.vx -= dx * force; b.vy -= dy * force; }
    }
    a.vx += (480 - a.x) * 0.0015; a.vy += (280 - a.y) * 0.0015;
  }
  state.relationship.edges.forEach((edge) => {
    const source = positions.get(edge.source); const target = positions.get(edge.target);
    if (!source || !target) return;
    const dx = target.x - source.x; const dy = target.y - source.y;
    const distance = Math.max(Math.hypot(dx, dy), 1);
    const force = (distance - 120) * 0.004;
    if (!source.fixed) { source.vx += (dx / distance) * force; source.vy += (dy / distance) * force; }
    if (!target.fixed) { target.vx -= (dx / distance) * force; target.vy -= (dy / distance) * force; }
  });
  nodes.forEach((node) => {
    const point = positions.get(node.id);
    if (!point || point.fixed) return;
    point.vx *= 0.86; point.vy *= 0.86;
    point.x = Math.max(35, Math.min(925, point.x + point.vx));
    point.y = Math.max(35, Math.min(525, point.y + point.vy));
  });
}

function relationshipPoint(event) {
  const rect = $("#relationship-graph").getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * 960;
  const y = ((event.clientY - rect.top) / rect.height) * 560;
  return { x: (x - state.relationship.tx) / state.relationship.scale, y: (y - state.relationship.ty) / state.relationship.scale };
}

function beginRelationshipNodeDrag(event) {
  if (event.button !== 0) return;
  event.stopPropagation();
  const id = event.currentTarget.dataset.nodeId;
  const point = state.relationship.positions.get(id);
  if (!point) return;
  point.fixed = true;
  state.relationship.pointer = { type: "node", id, moved: false };
  event.currentTarget.setPointerCapture(event.pointerId);
  $("#relationship-graph").classList.add("dragging-node");
}

function moveRelationshipPointer(event) {
  const pointer = state.relationship.pointer;
  if (!pointer) return;
  if (pointer.type === "node") {
    const point = state.relationship.positions.get(pointer.id);
    const next = relationshipPoint(event);
    if (point) { point.x = next.x; point.y = next.y; point.vx = 0; point.vy = 0; }
    pointer.moved = true;
    updateRelationshipPositions();
    return;
  }
  const rect = $("#relationship-graph").getBoundingClientRect();
  state.relationship.tx = pointer.tx + ((event.clientX - pointer.x) / rect.width) * 960;
  state.relationship.ty = pointer.ty + ((event.clientY - pointer.y) / rect.height) * 560;
  applyRelationshipTransform();
}

function endRelationshipPointer() {
  const pointer = state.relationship.pointer;
  if (pointer?.type === "node") {
    const point = state.relationship.positions.get(pointer.id);
    if (point) point.fixed = false;
    if (!state.relationship.paused) startRelationshipSimulation();
  }
  state.relationship.pointer = null;
  $("#relationship-graph").classList.remove("dragging-node", "panning");
}

function beginRelationshipPan(event) {
  if (event.button !== 0 || event.target.closest(".relationship-node")) return;
  state.relationship.pointer = { type: "pan", x: event.clientX, y: event.clientY, tx: state.relationship.tx, ty: state.relationship.ty };
  event.currentTarget.setPointerCapture(event.pointerId);
  event.currentTarget.classList.add("panning");
}

function zoomRelationshipGraph(event) {
  event.preventDefault();
  const before = relationshipPoint(event);
  const factor = event.deltaY < 0 ? 1.12 : 0.89;
  state.relationship.scale = Math.max(0.35, Math.min(3, state.relationship.scale * factor));
  const rect = $("#relationship-graph").getBoundingClientRect();
  const svgX = ((event.clientX - rect.left) / rect.width) * 960;
  const svgY = ((event.clientY - rect.top) / rect.height) * 560;
  state.relationship.tx = svgX - before.x * state.relationship.scale;
  state.relationship.ty = svgY - before.y * state.relationship.scale;
  applyRelationshipTransform();
}

function applyRelationshipTransform() {
  $("#relationship-viewport").setAttribute("transform", `translate(${state.relationship.tx} ${state.relationship.ty}) scale(${state.relationship.scale})`);
}

function fitRelationshipGraph() {
  const points = state.relationship.nodes.map((node) => state.relationship.positions.get(node.id)).filter(Boolean);
  if (!points.length) return;
  const xs = points.map((point) => point.x); const ys = points.map((point) => point.y);
  const minX = Math.min(...xs) - 55; const maxX = Math.max(...xs) + 55;
  const minY = Math.min(...ys) - 55; const maxY = Math.max(...ys) + 55;
  state.relationship.scale = Math.max(0.35, Math.min(1.8, Math.min(900 / Math.max(1, maxX - minX), 500 / Math.max(1, maxY - minY))));
  state.relationship.tx = 480 - ((minX + maxX) / 2) * state.relationship.scale;
  state.relationship.ty = 280 - ((minY + maxY) / 2) * state.relationship.scale;
  applyRelationshipTransform();
}

function resetRelationshipLayout() {
  initializeRelationshipPositions(state.relationship.nodes, true);
  state.relationship.scale = 1; state.relationship.tx = 0; state.relationship.ty = 0;
  state.relationship.paused = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  updateRelationshipPauseButton(); applyRelationshipTransform(); updateRelationshipPositions();
  if (!state.relationship.paused) startRelationshipSimulation();
}

function toggleRelationshipSimulation() {
  state.relationship.paused = !state.relationship.paused;
  if (state.relationship.paused) stopRelationshipSimulation(); else startRelationshipSimulation();
  updateRelationshipPauseButton();
}

function updateRelationshipPauseButton() {
  const button = $("#relationship-layout");
  button.setAttribute("aria-pressed", String(state.relationship.paused));
  button.textContent = state.relationship.paused ? "继续布局" : "暂停布局";
}

function moveRelationshipNodeWithKeyboard(event) {
  const deltas = { ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10] };
  if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRelationshipNode(event.currentTarget.dataset.nodeId); return; }
  if (!deltas[event.key]) return;
  event.preventDefault();
  const point = state.relationship.positions.get(event.currentTarget.dataset.nodeId);
  if (point) { point.x += deltas[event.key][0]; point.y += deltas[event.key][1]; updateRelationshipPositions(); }
}

function selectRelationshipNode(id) {
  state.relationship.selectedId = id;
  $$(".relationship-node").forEach((node) => node.classList.toggle("selected", node.dataset.nodeId === id));
  $$("#relationship-node-list button").forEach((button) => button.setAttribute("aria-current", String(button.dataset.nodeId === id)));
  renderRelationshipDetail();
}

function renderRelationshipNodeList() {
  const root = $("#relationship-node-list");
  root.replaceChildren();
  state.relationship.nodes.forEach((node) => {
    const item = document.createElement("div");
    item.setAttribute("role", "listitem");
    const button = document.createElement("button");
    button.type = "button"; button.dataset.nodeId = node.id; button.dataset.kind = node.kind;
    button.setAttribute("aria-current", String(node.id === state.relationship.selectedId));
    const label = document.createElement("span"); label.textContent = node.label;
    const meta = document.createElement("small");
    const degree = state.relationship.edges.filter((edge) => edge.source === node.id || edge.target === node.id).length;
    meta.textContent = `${relationshipKinds[node.kind] || "其他"} · ${degree} 条相邻关系`;
    button.append(label, meta); button.addEventListener("click", () => selectRelationshipNode(node.id));
    item.append(button); root.append(item);
  });
  if (!state.relationship.nodes.length) root.textContent = "没有可列出的节点";
}

function renderRelationshipDetail() {
  const root = $("#relationship-detail");
  root.replaceChildren();
  const node = state.relationship.nodes.find((item) => item.id === state.relationship.selectedId);
  if (!node) { root.textContent = "选择图中节点或下方列表查看相邻关系。"; return; }
  const heading = document.createElement("strong"); heading.textContent = node.label;
  const meta = document.createElement("p"); meta.textContent = `${node.type || relationshipKinds[node.kind]} · ${node.status || "active"}`;
  const description = document.createElement("p"); description.textContent = node.description || "暂无摘要";
  root.append(heading, meta, description);
  if (node.source_path) {
    const source = document.createElement("button"); source.type = "button"; source.className = "relationship-source";
    source.textContent = node.source_path; source.title = `打开 ${node.source_path}`;
    source.addEventListener("click", () => openDocument(node.source_path)); root.append(source);
  }
  const neighbors = state.relationship.edges.filter((edge) => edge.source === node.id || edge.target === node.id);
  const list = document.createElement("ul");
  neighbors.forEach((edge) => {
    const neighborId = edge.source === node.id ? edge.target : edge.source;
    const neighbor = state.relationship.nodes.find((item) => item.id === neighborId);
    const item = document.createElement("li"); item.textContent = `${neighbor?.label || neighborId}：${edge.label}`; list.append(item);
  });
  if (neighbors.length) root.append(list); else { const empty = document.createElement("p"); empty.textContent = "当前筛选中没有相邻关系。"; root.append(empty); }
}

async function createForeshadowing(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const payload = await api("/api/foreshadowing", {
      method: "POST",
      body: JSON.stringify({
        action: "create",
        node_id: $("#foreshadow-id").value.trim(),
        content: $("#foreshadow-content").value.trim(),
        weight: Number($("#foreshadow-weight").value),
        target_chapter: $("#foreshadow-target").value.trim(),
        created_at: state.workspace.snapshot.current_chapter,
      }),
    });
    state.continuity = payload.continuity;
    state.workspace = payload.workspace;
    renderContinuity();
    renderWorkspace();
    form.reset();
    $("#foreshadow-weight").value = "5";
    showToast("伏笔已加入连续性系统");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function extractSource(event) {
  event.preventDefault();
  const file = $("#source-file").files[0];
  const text = file ? await file.text() : $("#source-content").value;
  $("#source-extract").disabled = true;
  $("#source-status").textContent = "正在分块、提取并合并来源信号…";
  try {
    const payload = await api("/api/source", {
      method: "POST",
      body: JSON.stringify({ action: "extract", source_id: $("#source-id").value.trim(), focus: $("#source-focus").value, content: text }),
    });
    state.workspace = payload.workspace;
    renderWorkspace();
    $("#source-status").textContent = "来源包提取完成，可以审阅或晋升";
  } catch (error) {
    $("#source-status").textContent = error.message;
    showToast(error.message, true);
  } finally {
    $("#source-extract").disabled = false;
  }
}

async function runSourceAction(action, sourceId) {
  $("#source-status").textContent = "正在执行来源操作…";
  try {
    const payload = await api("/api/source", {
      method: "POST",
      body: JSON.stringify({ action, source_id: sourceId, target: "all" }),
    });
    state.workspace = payload.workspace;
    renderWorkspace();
    if (action === "review") $("#source-report").textContent = payload.result.review_report || "无报告";
    $("#source-status").textContent = action === "promote" ? "已晋升到当前作品" : action === "synthesize" ? "合成风格已刷新" : "来源报告已生成";
  } catch (error) {
    $("#source-status").textContent = error.message;
    showToast(error.message, true);
  }
}

async function runWriter(event) {
  event.preventDefault();
  const submit = $("#write-submit");
  const progress = $("#write-progress");
  submit.disabled = true;
  $("#write-cancel").disabled = true;
  progress.classList.remove("error");
  progress.textContent = "正在组装上下文并执行写作、观察和状态结算…";
  try {
    const payload = await api("/api/write", {
      method: "POST",
      body: JSON.stringify({
        chapter_id: $("#write-chapter-id").textContent,
        outline_revision: $("#write-outline-revision").value,
        guidance: $("#write-guidance").value,
        target_words: Number($("#write-words").value),
      }),
    });
    state.workspace = payload.workspace;
    renderWorkspace();
    $("#write-dialog").close();
    showToast(`${payload.result.chapter_id} 已完成，${formatNumber(payload.result.word_count)} 字`);
    if (payload.result.draft_path) {
      const match = state.workspace.documents.chapters.find((item) =>
        item.path.endsWith(`/${payload.result.chapter_id}.md`)
      );
      if (match) await openDocument(match.path, true);
    }
  } catch (error) {
    progress.classList.add("error");
    progress.textContent = error.message;
  } finally {
    submit.disabled = false;
    $("#write-cancel").disabled = false;
  }
}

async function runReview() {
  if (!state.document || state.dirty) {
    showToast(state.dirty ? "请先保存章节再审稿" : "未选择章节", true);
    return;
  }
  const dialog = $("#review-dialog");
  const loading = $("#review-loading");
  loading.hidden = false;
  loading.classList.remove("error");
  loading.textContent = "正在执行规则检查与深度审稿…";
  $("#review-result").hidden = true;
  dialog.showModal();
  try {
    const payload = await api("/api/review", {
      method: "POST",
      body: JSON.stringify({ path: state.document.path }),
    });
    state.workspace = payload.workspace;
    renderWorkspace();
    renderReview(payload.result);
  } catch (error) {
    $("#review-loading").textContent = error.message;
    $("#review-loading").classList.add("error");
  }
}

function renderReview(result) {
  $("#review-loading").hidden = true;
  $("#review-result").hidden = false;
  $("#review-score").textContent = String(Math.round(Number(result.score || 0)));
  const verdict = $("#review-verdict");
  verdict.textContent = result.passed ? "通过" : "需要修订";
  verdict.classList.toggle("ready", Boolean(result.passed));
  $("#review-summary").textContent = result.summary || `${result.issues || 0} 个问题`;
  const root = $("#review-issues");
  root.replaceChildren();
  const issues = result.issue_details || [];
  if (!issues.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "未发现需要处理的问题";
    root.append(empty);
    return;
  }
  issues.forEach((issue) => {
    const item = document.createElement("article");
    item.className = "review-issue";
    const heading = document.createElement("div");
    heading.className = "review-issue-heading";
    const category = document.createElement("strong");
    category.textContent = issue.category || "未分类";
    const severity = document.createElement("span");
    const severityName = ["critical", "warning", "info"].includes(issue.severity)
      ? issue.severity : "warning";
    severity.className = `severity ${severityName}`;
    severity.textContent = { critical: "严重", warning: "警告", info: "提示" }[severityName];
    heading.append(category, severity);
    const description = document.createElement("p");
    description.textContent = issue.description || "";
    item.append(heading, description);
    if (issue.suggestion) {
      const suggestion = document.createElement("p");
      suggestion.className = "review-suggestion";
      suggestion.textContent = `建议：${issue.suggestion}`;
      item.append(suggestion);
    }
    root.append(item);
  });
}

function toggleInspector(open) {
  const inspector = $("#inspector");
  inspector.classList.toggle("open", open);
  $("#inspector-toggle").setAttribute("aria-expanded", String(open));
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $(".brand-logo").src = theme === "dark" ? "/brand/logo-dark.svg" : "/brand/logo.svg";
  localStorage.setItem("openwrite-theme", theme);
}

function bindEvents() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  $$('[data-switch-view]').forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.switchView));
  });
  $("#document-editor").addEventListener("input", () => {
    state.dirty = true;
    setSaveState("未保存", true);
    updateEditorCount();
  });
  $("#save-document").addEventListener("click", saveDocument);
  $("#review-document").addEventListener("click", runReview);
  $("#reload-document").addEventListener("click", () => {
    if (state.document) openDocument(state.document.path, false);
  });
  $("#focus-form").addEventListener("submit", saveFocus);
  $("#model-state").addEventListener("click", () => $("#model-dialog").showModal());
  $("#model-close").addEventListener("click", () => $("#model-dialog").close());
  $("#model-cancel").addEventListener("click", () => $("#model-dialog").close());
  $("#model-form").addEventListener("submit", saveModel);
  $("#project-form").addEventListener("submit", initializeProject);
  $("#open-project-form").addEventListener("submit", openProject);
  $("#search-form").addEventListener("submit", searchProject);
  $("#project-dialog").addEventListener("cancel", (event) => {
    if (!state.workspace?.initialized) event.preventDefault();
  });
  $("#write-open").addEventListener("click", () => openSmartWriteDialog());
  $("#outline-tree-back").addEventListener("click", () => setView("outline"));
  $("#outline-add-volume").addEventListener("click", () => openOutlineEditDialog("add_child"));
  $("#outline-source").addEventListener("click", () => openOutlineSource());
  $("#outline-refresh").addEventListener("click", () => loadOutline());
  $("#outline-smart-create").addEventListener("click", () => openSmartWriteDialog());
  $("#outline-node-source").addEventListener("click", () => openOutlineSource(selectedOutlineNode()?.line || 1));
  $("#outline-node-create").addEventListener("click", () => openSmartWriteDialog(selectedOutlineNode()?.id || ""));
  $("#outline-node-rename").addEventListener("click", () => openOutlineEditDialog("rename", selectedOutlineNode()));
  $("#outline-node-add-child").addEventListener("click", () => openOutlineEditDialog("add_child", selectedOutlineNode()));
  $("#outline-node-add-after").addEventListener("click", () => openOutlineEditDialog("add_after", selectedOutlineNode()));
  $("#outline-node-delete").addEventListener("click", () => openOutlineEditDialog("delete", selectedOutlineNode()));
  $("#outline-edit-close").addEventListener("click", () => $("#outline-edit-dialog").close());
  $("#outline-edit-cancel").addEventListener("click", () => $("#outline-edit-dialog").close());
  $("#outline-edit-form").addEventListener("submit", submitOutlineEdit);
  $("#write-close").addEventListener("click", () => $("#write-dialog").close());
  $("#write-cancel").addEventListener("click", () => $("#write-dialog").close());
  $("#write-form").addEventListener("submit", runWriter);
  $("#review-close").addEventListener("click", () => $("#review-dialog").close());
  $("#sync-project").addEventListener("click", runSync);
  $("#context-form").addEventListener("submit", inspectContext);
  $("#import-form").addEventListener("submit", importText);
  $("#create-document-form").addEventListener("submit", createDocument);
  $("#chat-form").addEventListener("submit", submitChat);
  $$('[data-agent]').forEach((button) => button.addEventListener("click", () => chooseAgent(button.dataset.agent)));
  $("#continuity-refresh").addEventListener("click", loadContinuity);
  $("#relationship-filter").addEventListener("change", () => renderRelationshipGraph(state.continuity?.relationship_graph || {}));
  $("#relationship-fit").addEventListener("click", fitRelationshipGraph);
  $("#relationship-layout").addEventListener("click", toggleRelationshipSimulation);
  $("#relationship-reset").addEventListener("click", resetRelationshipLayout);
  $("#relationship-graph").addEventListener("pointerdown", beginRelationshipPan);
  $("#relationship-graph").addEventListener("pointermove", moveRelationshipPointer);
  $("#relationship-graph").addEventListener("pointerup", endRelationshipPointer);
  $("#relationship-graph").addEventListener("pointercancel", endRelationshipPointer);
  $("#relationship-graph").addEventListener("wheel", zoomRelationshipGraph, { passive: false });
  $("#foreshadow-form").addEventListener("submit", createForeshadowing);
  $("#source-form").addEventListener("submit", extractSource);
  $("#source-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (file) $("#source-content").value = await file.text();
  });
  $("#inspector-toggle").addEventListener("click", () => toggleInspector(true));
  $("#inspector-close").addEventListener("click", () => toggleInspector(false));
  $("#theme-toggle").addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveDocument();
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      setView("search");
      $("#search-query").focus();
    }
    if (event.key === "Escape") toggleInspector(false);
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) event.preventDefault();
  });
  window.addEventListener("popstate", routeFromLocation);
}

async function routeFromLocation() {
  const hash = decodeURIComponent(location.hash.slice(1));
  if (hash.startsWith("doc=")) {
    await openDocument(hash.slice(4), false);
  } else if (["search", "outline", "chapters", "story", "characters", "world", "agents", "continuity", "tools"].includes(hash)) {
    setView(hash, false);
  } else {
    setView("dashboard", false);
  }
}

async function start() {
  const storedTheme = localStorage.getItem("openwrite-theme");
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(storedTheme || (systemDark ? "dark" : "light"));
  bindEvents();
  try {
    await loadWorkspace();
    await routeFromLocation();
  } catch (error) {
    document.querySelector("#app").setAttribute("aria-busy", "false");
    showToast(error.message, true);
  }
}

start();
