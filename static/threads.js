/**
 * threads.js
 * Shared utilities + main threads list page logic.
 */

/* ─── Shared Utilities ──────────────────────────────────────────────────── */

const CFG = window.THREADS_CONFIG || {};
let _currentUser = CFG.user || null;

function showToast(msg, isError = false) {
  const el   = document.getElementById("appToast");
  const body = document.getElementById("appToastBody");
  if (!el || !body) return;
  body.textContent = msg;
  el.classList.toggle("text-bg-danger", isError);
  el.classList.toggle("text-bg-dark", !isError);
  new bootstrap.Toast(el, { delay: 3000 }).show();
}

function formatRelTime(tsStr) {
  if (!tsStr) return "";
  const d = new Date(tsStr.endsWith("Z") ? tsStr : tsStr + "Z");
  const now = Date.now();
  const diff = Math.floor((now - d.getTime()) / 1000);
  if (diff < 60)    return `${diff}d lalu`;
  if (diff < 3600)  return `${Math.floor(diff/60)}m lalu`;
  if (diff < 86400) return `${Math.floor(diff/3600)}j lalu`;
  return d.toLocaleDateString("id-ID", { day:"numeric", month:"short", year:"numeric" });
}

function renderRelTimes() {
  document.querySelectorAll("[data-ts]").forEach(el => {
    el.textContent = formatRelTime(el.getAttribute("data-ts"));
  });
}

function avatarHTML(username, profilePic, size = "md") {
  const cls = size === "sm" ? "thread-avatar-sm-placeholder"
            : size === "xs" ? "thread-avatar-xs-placeholder"
            :                 "thread-avatar-placeholder";
  if (profilePic) {
    return `<img src="${profilePic}" class="thread-avatar" alt="avatar" onerror="this.style.display='none'">`;
  }
  const letter = (username || "?")[0].toUpperCase();
  return `<div class="${cls}">${letter}</div>`;
}

async function apiJSON(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ─── Profile Modal ─────────────────────────────────────────────────────── */

let _profileCallback = null;

function openProfileModal(callback) {
  _profileCallback = callback || null;
  const modal = new bootstrap.Modal(document.getElementById("profileModal"));
  if (_currentUser) {
    document.getElementById("profileUsername").value = _currentUser.username || "";
  }
  modal.show();
}

function closeProfileModal() {
  const el = document.getElementById("profileModal");
  bootstrap.Modal.getInstance(el)?.hide();
}

document.addEventListener("DOMContentLoaded", () => {
  // ── Profile form: preview ────────────────────────────────────────────────
  const picInput   = document.getElementById("profilePicInput");
  const picPreview = document.getElementById("profilePicPreview");
  if (picInput && picPreview) {
    picInput.addEventListener("change", () => {
      const file = picInput.files[0];
      if (file) {
        picPreview.src = URL.createObjectURL(file);
        picPreview.classList.remove("d-none");
      }
    });
  }

  // ── Profile form: cancel ─────────────────────────────────────────────────
  const cancelBtn = document.getElementById("btnProfileCancel");
  if (cancelBtn) {
    cancelBtn.addEventListener("click", closeProfileModal);
  }

  // ── Profile form: save ───────────────────────────────────────────────────
  const saveBtn = document.getElementById("btnProfileSave");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const spinner = document.getElementById("profileSaveSpinner");
      const errEl   = document.getElementById("profileFormError");
      const form    = document.getElementById("profileForm");
      if (!form) return;

      const username = (document.getElementById("profileUsername")?.value || "").trim();
      if (!username) {
        if (errEl) { errEl.textContent = "Username wajib diisi."; errEl.classList.remove("d-none"); }
        return;
      }

      if (spinner) spinner.classList.remove("d-none");
      saveBtn.disabled = true;

      try {
        const fd = new FormData(form);
        const data = await apiJSON("/api/user/update", { method: "POST", body: fd });
        _currentUser = { username: data.username, profile_pic: data.profile_pic };
        closeProfileModal();
        showToast("Profil tersimpan!");
        // refresh composer strip if on list page
        if (typeof _refreshComposer === "function") _refreshComposer();
        if (_profileCallback) { _profileCallback(); _profileCallback = null; }
      } catch (e) {
        if (errEl) { errEl.textContent = e.message; errEl.classList.remove("d-none"); }
      } finally {
        if (spinner) spinner.classList.add("d-none");
        saveBtn.disabled = false;
      }
    });
  }

  renderRelTimes();
  setInterval(renderRelTimes, 60000);
});

/* ─── Threads List Page ─────────────────────────────────────────────────── */

if (document.getElementById("threadList") !== null) {
  let _page      = 1;
  let _total     = 0;
  let _perPage   = 20;
  let _topic     = "";
  let _loading   = false;

  async function loadThreads(reset = false) {
    if (_loading) return;
    _loading = true;

    if (reset) {
      _page = 1;
      document.getElementById("threadList").innerHTML = "";
    }

    try {
      const url = `${CFG.apiBase}/hub?page=${_page}&per_page=${_perPage}&topic=${encodeURIComponent(_topic)}`;
      const data = await apiJSON(url);
      _total = data.total;

      const container = document.getElementById("threadList");
      const emptyEl   = document.getElementById("threadListEmpty");

      if (data.threads.length === 0 && _page === 1) {
        if (emptyEl) emptyEl.classList.remove("d-none");
      } else {
        if (emptyEl) emptyEl.classList.add("d-none");
        data.threads.forEach(t => container.appendChild(buildThreadCard(t)));
        renderRelTimes();
      }

      const loadMore = document.getElementById("loadMoreContainer");
      if (_page * _perPage < _total) {
        loadMore?.classList.remove("d-none");
      } else {
        loadMore?.classList.add("d-none");
      }
    } catch (e) {
      showToast(e.message, true);
    } finally {
      _loading = false;
    }
  }

  function buildThreadCard(t) {
    const div = document.createElement("div");
    div.className = "thread-card mb-3 new-item";
    div.dataset.id = t.thread_id;

    const owned = (t.user_ip === CFG.currentIp) || false;
    const likedClass  = t.liked ? "liked" : "";
    const heartIcon   = t.liked ? "bi-heart-fill text-danger" : "bi-heart";
    const topicBadge  = t.topic ? `<span><i class="bi bi-chevron-right"></i> ${escHtml(t.topic)}</span>` : "";
    const imgHtml     = t.image_url
      ? `<img src="${t.image_url}" class="img-fluid thread-img mb-2" alt="thread image">`
      : "";
    const menuHtml    = owned ? `
      <div class="dropdown">
        <button class="btn btn-link btn-sm p-0 text-muted" data-bs-toggle="dropdown">
          <i class="bi bi-three-dots-vertical"></i>
        </button>
        <ul class="dropdown-menu dropdown-menu-end">
          <li><button class="dropdown-item small" onclick="openEditThread('${t.thread_id}')"><i class="bi bi-pencil me-2"></i>Edit</button></li>
          <li><button class="dropdown-item small text-danger" onclick="deleteThread('${t.thread_id}')"><i class="bi bi-trash me-2"></i>Hapus</button></li>
        </ul>
      </div>` : "";

    div.innerHTML = `
      <div class="d-flex gap-3">
        <div class="flex-shrink-0">
          ${avatarHTML(t.username_snapshot, t.avatar_url || "", "md")}
        </div>
        <div class="flex-grow-1">
          <div class="d-flex justify-content-between align-items-start">
            <div>
              <a href="/post/${t.thread_id}" class="fw-bold text-decoration-none text-dark">${escHtml(t.username_snapshot)}</a>
              ${topicBadge}
              <span class="text-muted small ms-2" data-ts="${t.created_at}"></span>
            </div>
            ${menuHtml}
          </div>
          <a href="/post/${t.thread_id}" class="text-decoration-none text-dark">
            <p class="mt-2 mb-2 thread-text">${escHtml(t.text)}</p>
            ${imgHtml}
          </a>
          <div class="d-flex gap-3 mt-2 thread-actions">
            <button class="btn btn-link btn-sm p-0 text-muted like-btn ${likedClass}"
                    data-id="${t.thread_id}" data-kind="thread" onclick="toggleLike(this)">
              <i class="bi ${heartIcon}"></i>
              <span class="like-count">${t.like_count}</span>
            </button>
            <a href="/post/${t.thread_id}" class="btn btn-link btn-sm p-0 text-muted">
              <i class="bi bi-chat"></i>
              <span>${t.comment_count ?? 0}</span>
            </a>
            <button class="btn btn-link btn-sm p-0 text-muted" onclick="shareThread('${t.thread_id}', this)">
              <i class="bi bi-share"></i>
              <span>${t.share_count}</span>
            </button>
          </div>
        </div>
      </div>`;
    return div;
  }

  // ── Profile button ─────────────────────────────────────────────────────
  document.getElementById("btnOpenProfile")?.addEventListener("click", () => {
    openProfileModal(() => _refreshComposer());
  });

  // ── Composer strip ────────────────────────────────────────────────────────
  function _refreshComposer() {
    const avatarEl = document.getElementById("composerAvatar");
    const nameEl   = document.getElementById("composerName");
    const labelEl  = document.getElementById("navProfileLabel");
    if (!avatarEl || !nameEl) return;
    if (_currentUser) {
      avatarEl.innerHTML = avatarHTML(_currentUser.username, _currentUser.profile_pic, "md");
      nameEl.textContent = _currentUser.username;
      if (labelEl) labelEl.textContent = _currentUser.username;
    } else {
      avatarEl.innerHTML = avatarHTML("?", "", "md");
      nameEl.textContent = "";
      if (labelEl) labelEl.textContent = "Profile";
    }
  }

  document.getElementById("composerStrip")?.addEventListener("click", () => {
    if (!_currentUser) {
      openProfileModal(() => { _refreshComposer(); openThreadModal(); });
    } else {
      openThreadModal();
    }
  });

  // Init composer on page load
  _refreshComposer();

  function openThreadModal(thread = null) {
    document.getElementById("threadModalTitle").innerHTML = thread
      ? '<i class="bi bi-pencil me-2"></i>Edit Thread'
      : '<i class="bi bi-plus-circle me-2"></i>New Thread';
    document.getElementById("threadModalId").value  = thread ? thread.thread_id : "";
    document.getElementById("threadText").value     = thread ? thread.text : "";
    document.getElementById("threadTopic").value    = thread ? (thread.topic || "") : "";
    document.getElementById("threadFormError").classList.add("d-none");
    document.getElementById("btnThreadSave").innerHTML =
      `<span id="threadSaveSpinner" class="spinner-border spinner-border-sm d-none me-1"></span>${thread ? "Simpan" : "Post"}`;
    new bootstrap.Modal(document.getElementById("threadModal")).show();
  }

  window.openEditThread = async function(thread_id) {
    try {
      // fetch thread details
      const res = await fetch(`/post/${thread_id}`);
      // We don't have a GET /api/hub/:id, re-use the page link
      // Instead, find it in DOM
      const card = document.querySelector(`[data-id="${thread_id}"]`);
      if (!card) return;
      openThreadModal({
        thread_id: thread_id,
        text:  card.querySelector(".thread-text")?.textContent || "",
        topic: card.querySelector(".badge")?.textContent || "",
      });
    } catch(e) { showToast(e.message, true); }
  };

  // Image preview
  document.getElementById("threadImage")?.addEventListener("change", function() {
    const wrap = document.getElementById("threadImagePreviewWrap");
    const prev = document.getElementById("threadImagePreview");
    if (this.files[0]) {
      prev.src = URL.createObjectURL(this.files[0]);
      wrap.classList.remove("d-none");
    } else {
      wrap.classList.add("d-none");
    }
  });

  // Save thread
  document.getElementById("btnThreadSave")?.addEventListener("click", async () => {
    const errEl   = document.getElementById("threadFormError");
    const threadId = document.getElementById("threadModalId").value;
    const text  = document.getElementById("threadText").value.trim();
    const topic = document.getElementById("threadTopic").value.trim();
    const imgInput = document.getElementById("threadImage");

    if (!text) {
      errEl.textContent = "Text wajib diisi.";
      errEl.classList.remove("d-none");
      return;
    }

    const spinner = document.getElementById("threadSaveSpinner");
    const saveBtn  = document.getElementById("btnThreadSave");
    spinner?.classList.remove("d-none");
    if (saveBtn) saveBtn.disabled = true;

    const fd = new FormData();
    fd.append("text", text);
    fd.append("topic", topic);
    if (imgInput?.files[0]) fd.append("image", imgInput.files[0]);

    try {
      let data;
      if (threadId) {
        data = await apiJSON(`/api/hub/${threadId}`, { method: "PUT", body: fd });
        // update in DOM
        const card = document.querySelector(`[data-id="${threadId}"]`);
        if (card) {
          card.querySelector(".thread-text").textContent = data.text;
          const badge = card.querySelector(".badge");
          if (data.topic) {
            if (badge) badge.textContent = data.topic;
          }
        }
      } else {
        data = await apiJSON("/api/hub", { method: "POST", body: fd });
        const card = buildThreadCard(data);
        document.getElementById("threadList").prepend(card);
        renderRelTimes();
      }
      bootstrap.Modal.getInstance(document.getElementById("threadModal")).hide();
      showToast(threadId ? "Thread diperbarui." : "Thread diposting!");
      document.getElementById("threadForm").reset();
      document.getElementById("threadImagePreviewWrap").classList.add("d-none");
    } catch (e) {
      errEl.textContent = e.message;
      errEl.classList.remove("d-none");
    } finally {
      document.getElementById("threadSaveSpinner")?.classList.add("d-none");
      const b = document.getElementById("btnThreadSave");
      if (b) b.disabled = false;
    }
  });

  // Delete thread
  window.deleteThread = async function(thread_id) {
    if (!confirm("Hapus thread ini?")) return;
    try {
      await apiJSON(`/api/hub/${thread_id}`, { method: "DELETE" });
      document.querySelector(`[data-id="${thread_id}"]`)?.remove();
      showToast("Thread dihapus.");
    } catch(e) { showToast(e.message, true); }
  };

  // Share thread
  window.shareThread = async function(thread_id, btn) {
    try {
      const data = await apiJSON("/api/share/thread", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id }),
      });
      await navigator.clipboard.writeText(data.share_url);
      showToast("Link disalin ke clipboard!");
      const countEl = btn.querySelector("span");
      if (countEl) countEl.textContent = parseInt(countEl.textContent || "0") + 1;
    } catch(e) { showToast(e.message, true); }
  };

  // Topic filter
  document.getElementById("btnFilterTopic")?.addEventListener("click", () => {
    _topic = document.getElementById("topicFilter").value.trim();
    loadThreads(true);
  });
  document.getElementById("btnClearFilter")?.addEventListener("click", () => {
    _topic = "";
    document.getElementById("topicFilter").value = "";
    loadThreads(true);
  });
  document.getElementById("topicFilter")?.addEventListener("keydown", e => {
    if (e.key === "Enter") document.getElementById("btnFilterTopic").click();
  });

  // Load more
  document.getElementById("btnLoadMore")?.addEventListener("click", () => {
    _page++;
    loadThreads(false);
  });

  // Init
  loadThreads(true);
}

/* ─── Shared: Like ──────────────────────────────────────────────────────── */

window.toggleLike = async function(btn) {
  if (!_currentUser) {
    openProfileModal(() => toggleLike(btn));
    return;
  }
  const id   = btn.dataset.id;
  const kind = btn.dataset.kind;
  const endpoint = kind === "thread" ? "/api/like/thread" : "/api/like/comment";
  const body = kind === "thread" ? { thread_id: id } : { comment_id: id };

  try {
    const data = await apiJSON(endpoint, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });
    const countEl = btn.querySelector(".like-count");
    const icon    = btn.querySelector("i");
    if (countEl) countEl.textContent = data.count;
    if (data.liked) {
      btn.classList.add("liked");
      if (icon) { icon.className = "bi bi-heart-fill text-danger"; }
    } else {
      btn.classList.remove("liked");
      if (icon) { icon.className = "bi bi-heart"; }
    }
  } catch(e) { showToast(e.message, true); }
};

/* ─── Shared: Copy share URL ─────────────────────────────────────────────── */

window.copyShareUrl = async function(thread_id) {
  try {
    const data = await apiJSON("/api/share/thread", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ thread_id }),
    });
    await navigator.clipboard.writeText(data.share_url);
    showToast("Link disalin ke clipboard!");
  } catch(e) { showToast(e.message, true); }
};

/* ─── HTML escape helper ─────────────────────────────────────────────────── */

function escHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
