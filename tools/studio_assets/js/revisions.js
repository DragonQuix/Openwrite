import { $, api, formatNumber, showToast, state } from "/js/core.js";
import { enqueueTask } from "/js/tasks.js";

let refreshWorkspace = async () => {};
let reopenDocument = async () => {};

export function bindRevisionUI(callbacks = {}) {
  refreshWorkspace = callbacks.refreshWorkspace || refreshWorkspace;
  reopenDocument = callbacks.reopenDocument || reopenDocument;
  $("#revision-selection").addEventListener("click", () => openRevisionRequest(false));
  $("#revision-full-chapter").addEventListener("click", () => openRevisionRequest(true));
  $("#revision-history").addEventListener("click", openRevisionHistory);
  $("#revision-request-close").addEventListener("click", closeRevisionRequest);
  $("#revision-request-cancel").addEventListener("click", closeRevisionRequest);
  $("#revision-request-form").addEventListener("submit", submitRevisionRequest);
  $("#revision-preview-close").addEventListener("click", closeRevisionPreview);
  $("#revision-apply").addEventListener("click", applyCurrentRevision);
  $("#revision-reject").addEventListener("click", rejectCurrentRevision);
  $("#revision-regenerate").addEventListener("click", regenerateCurrentRevision);
  $("#revision-history-close").addEventListener("click", () => $("#revision-history-dialog").close());
}

export function syncRevisionControls() {
  const chapter = state.document && state.document.path.startsWith("data/manuscript/");
  const configured = Boolean(state.workspace?.model?.configured);
  const editor = $("#document-editor");
  const hasSelection = chapter && editor.selectionEnd > editor.selectionStart;
  $("#revision-selection").hidden = !chapter;
  $("#revision-selection").disabled = !configured || !hasSelection || state.dirty;
  $("#revision-full-chapter").hidden = !chapter;
  $("#revision-full-chapter").disabled = !configured || state.dirty;
  $("#revision-history").hidden = !chapter;
}

export function appendReviewIssueActions(container, issue, reviewResult) {
  const actions = document.createElement("div");
  actions.className = "review-issue-actions";
  const locate = document.createElement("button");
  locate.type = "button";
  locate.className = "quiet-button";
  locate.textContent = "定位原文";
  locate.disabled = !issueAnchor(issue);
  locate.addEventListener("click", () => locateIssue(issue));
  const revise = document.createElement("button");
  revise.type = "button";
  revise.className = "quiet-button";
  revise.textContent = "生成修订提案";
  revise.disabled = !issue.auto_fixable || !issueAnchor(issue) || !state.workspace?.model?.configured;
  revise.addEventListener("click", () => createReviewRevision(issue, reviewResult));
  actions.append(locate, revise);
  container.append(actions);
}

function openRevisionRequest(fullChapter) {
  if (!state.document || state.dirty) {
    showToast(state.dirty ? "请先保存当前章节" : "请先打开章节", true);
    return;
  }
  const editor = $("#document-editor");
  const start = fullChapter ? 0 : editor.selectionStart;
  const end = fullChapter ? editor.value.length : editor.selectionEnd;
  if (end <= start) {
    showToast("请先选择需要修改的文字", true);
    return;
  }
  $("#revision-full-mode").value = fullChapter ? "1" : "0";
  $("#revision-selection-start").value = String(codePointOffset(editor.value, start));
  $("#revision-selection-end").value = String(codePointOffset(editor.value, end));
  $("#revision-original-text").value = editor.value.slice(start, end);
  $("#revision-request-title").textContent = fullChapter ? "整章修订" : "修订所选文字";
  $("#revision-selection-preview").textContent = editor.value.slice(start, end);
  $("#revision-request-status").textContent = fullChapter
    ? "整章修订可能影响后续连续性，应用前请逐段检查差异。"
    : "提案不会直接覆盖正文，生成后需要再次确认。";
  $("#revision-request-dialog").showModal();
}

function closeRevisionRequest() {
  $("#revision-request-dialog").close();
}

async function submitRevisionRequest(event) {
  event.preventDefault();
  const button = $("#revision-request-submit");
  button.disabled = true;
  $("#revision-request-status").textContent = "正在生成修订提案…";
  try {
    await enqueueTask(
      "revision_selection",
      {
        chapter_id: chapterId(),
        start: Number($("#revision-selection-start").value),
        end: Number($("#revision-selection-end").value),
        original_text: $("#revision-original-text").value,
        action: $("#revision-action").value,
        instruction: $("#revision-instruction").value,
        target_units: Number($("#revision-target-units").value || 0),
        full_chapter: $("#revision-full-mode").value === "1",
      },
      {
        label: "修订提案任务已加入队列",
        onComplete: (task) => showRevisionPreview(task.result),
      },
    );
    closeRevisionRequest();
  } catch (error) {
    $("#revision-request-status").textContent = error.message;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function createReviewRevision(issue, reviewResult) {
  const chapter = reviewResult.chapter_id || chapterId();
  try {
    await enqueueTask(
      "revision_from_review",
      { chapter_id: chapter, issue_ids: [issue.id] },
      {
        label: "审稿修订任务已加入队列",
        onComplete: (task) => showRevisionPreview(task.result),
      },
    );
    $("#review-dialog").close();
  } catch (error) {
    showToast(error.message, true);
  }
}

export function showRevisionPreview(proposal) {
  if ($("#task-center-dialog")?.open) $("#task-center-dialog").close();
  state.revisionProposal = proposal;
  $("#revision-preview-title").textContent = revisionKindLabel(proposal.kind);
  $("#revision-preview-status").textContent = statusLabel(proposal.status);
  $("#revision-before").textContent = proposal.selection?.original_text || "";
  $("#revision-after").textContent = proposal.replacement_text || "";
  $("#revision-rationale").textContent = proposal.rationale || "未提供修改说明";
  const risks = proposal.risk_flags || [];
  $("#revision-risks").textContent = risks.length ? `风险提示：${risks.join("；")}` : "未标记额外风险";
  const stats = proposal.diff?.stats || {};
  $("#revision-stats").textContent = `${formatNumber(stats.removed_units)} → ${formatNumber(stats.added_units)} 字符`;
  $("#revision-unified-diff").textContent = proposal.diff?.unified || "没有文本差异";
  const proposed = proposal.status === "proposed";
  $("#revision-apply").disabled = !proposed;
  $("#revision-reject").disabled = !["proposed", "stale"].includes(proposal.status);
  $("#revision-regenerate").disabled = proposal.status === "applied";
  $("#revision-preview-dialog").showModal();
}

function closeRevisionPreview() {
  $("#revision-preview-dialog").close();
}

async function applyCurrentRevision() {
  const proposal = state.revisionProposal;
  if (!proposal) return;
  const button = $("#revision-apply");
  button.disabled = true;
  $("#revision-preview-status").textContent = "正在校验并应用…";
  try {
    const payload = await api(`/api/revisions/${encodeURIComponent(proposal.proposal_id)}/apply`, {
      method: "POST",
      body: "{}",
    });
    state.revisionProposal = payload.data.proposal;
    state.dirty = false;
    closeRevisionPreview();
    await refreshWorkspace();
    await reopenDocument(payload.data.proposal.document.path);
    showToast("修订已应用，原审稿结果已标记待刷新");
  } catch (error) {
    $("#revision-preview-status").textContent = error.code === "DOCUMENT_CONFLICT"
      ? "原文已变化，请重新生成提案"
      : error.message;
    showToast(error.message, true);
    if (error.code === "DOCUMENT_CONFLICT") button.disabled = true;
  }
}

async function rejectCurrentRevision() {
  const proposal = state.revisionProposal;
  if (!proposal) return;
  try {
    const payload = await api(`/api/revisions/${encodeURIComponent(proposal.proposal_id)}/reject`, {
      method: "POST",
      body: "{}",
    });
    closeRevisionPreview();
    showToast(payload.data.status === "rejected" ? "修订提案已放弃" : "提案状态已更新");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function regenerateCurrentRevision() {
  const proposal = state.revisionProposal;
  if (!proposal) return;
  $("#revision-preview-status").textContent = "正在重新生成…";
  try {
    const payload = await api(`/api/revisions/${encodeURIComponent(proposal.proposal_id)}/regenerate`, {
      method: "POST",
      body: "{}",
    });
    closeRevisionPreview();
    showRevisionPreview(payload.data);
  } catch (error) {
    $("#revision-preview-status").textContent = error.message;
    showToast(error.message, true);
  }
}

async function openRevisionHistory() {
  if (!state.document) return;
  const dialog = $("#revision-history-dialog");
  const root = $("#revision-history-list");
  root.textContent = "正在读取提案…";
  dialog.showModal();
  try {
    const payload = await api(`/api/revisions?chapter=${encodeURIComponent(chapterId())}`);
    renderRevisionHistory(payload.data.proposals || []);
  } catch (error) {
    root.textContent = error.message;
  }
}

function renderRevisionHistory(proposals) {
  const root = $("#revision-history-list");
  root.replaceChildren();
  if (!proposals.length) {
    root.textContent = "本章还没有修订提案。";
    return;
  }
  proposals.forEach((proposal) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "revision-history-item";
    const title = document.createElement("strong");
    title.textContent = revisionKindLabel(proposal.kind);
    const meta = document.createElement("span");
    meta.textContent = `${statusLabel(proposal.status)} · ${new Date(proposal.created_at).toLocaleString("zh-CN")}`;
    button.append(title, meta);
    button.addEventListener("click", () => {
      $("#revision-history-dialog").close();
      showRevisionPreview(proposal);
    });
    root.append(button);
  });
}

function locateIssue(issue) {
  const anchor = issueAnchor(issue);
  if (!anchor) {
    showToast("该问题没有可定位的正文证据", true);
    return;
  }
  const editor = $("#document-editor");
  const [start, end] = anchor;
  $("#review-dialog").close();
  editor.focus();
  editor.setSelectionRange(start, end);
  const lineHeight = Number.parseFloat(getComputedStyle(editor).lineHeight) || 28;
  const line = editor.value.slice(0, start).split("\n").length - 1;
  editor.scrollTop = Math.max(0, line * lineHeight - editor.clientHeight / 3);
  syncRevisionControls();
}

function issueAnchor(issue) {
  if (!state.document) return null;
  const content = $("#document-editor").value;
  const quote = issue.evidence?.quote || "";
  const start = Number(issue.anchor?.start_hint);
  const end = Number(issue.anchor?.end_hint);
  if (Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end > start) {
    const jsStart = utf16Offset(content, start);
    const jsEnd = utf16Offset(content, end);
    if (!quote || content.slice(jsStart, jsEnd) === quote) return [jsStart, jsEnd];
  }
  if (quote) {
    const found = content.indexOf(quote);
    if (found >= 0 && content.indexOf(quote, found + 1) < 0) return [found, found + quote.length];
  }
  return null;
}

function codePointOffset(text, utf16Index) {
  return Array.from(text.slice(0, utf16Index)).length;
}

function utf16Offset(text, codePointIndex) {
  return Array.from(text).slice(0, codePointIndex).join("").length;
}

function chapterId() {
  return state.document?.path.match(/\/(ch_\d+)\.md$/)?.[1] || "";
}

function revisionKindLabel(kind) {
  return {
    selection_rewrite: "局部修订提案",
    review_fix: "审稿问题修订",
    full_chapter_revision: "整章修订提案",
  }[kind] || "修订提案";
}

function statusLabel(status) {
  return { proposed: "待确认", applied: "已应用", rejected: "已放弃", stale: "已失效" }[status] || status;
}
