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
    const topicBadge  = t.topic ? `<button class="topic-badge-btn" onclick="filterByTopic('${escHtml(t.topic)}')"><i class="bi bi-chevron-right"></i> ${escHtml(t.topic)}</button>` : "";
    const _imgs = Array.isArray(t.image_urls) && t.image_urls.length ? t.image_urls
                : (t.image_url ? [t.image_url] : []);
    const imgHtml = _imgs.length
      ? `<div class="thread-img-scroll">${_imgs.map(u => `<img src="${u}" class="thread-img-thumb" alt="thread image">`).join("")}</div>`
      : "";
    const fileHtml = buildFileAttachmentsHtml(t.file_items || []);
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
              <span class="fw-bold text-decoration-none text-dark">${escHtml(t.username_snapshot)}</span>
              ${topicBadge}
              <span class="text-muted small ms-2" data-ts="${t.created_at}"></span>
            </div>
            ${menuHtml}
          </div>
          <p class="mt-2 mb-2 thread-text">${linkify(t.text)}</p>
          ${imgHtml}
          ${fileHtml}
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

  // Image preview (multi)
  document.getElementById("threadImages")?.addEventListener("change", function() {
    const wrap = document.getElementById("threadImagesPreviewWrap");
    const grid = document.getElementById("threadImagesPreviewGrid");
    const MAX = 20;
    const files = Array.from(this.files).slice(0, MAX);
    grid.innerHTML = "";
    if (!files.length) { wrap.classList.add("d-none"); return; }
    files.forEach(file => {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.className = "img-thumbnail";
      img.style.maxHeight = "90px";
      img.style.maxWidth  = "120px";
      img.alt = "preview";
      grid.appendChild(img);
    });
    wrap.classList.remove("d-none");
  });

  // File preview
  document.getElementById("threadFiles")?.addEventListener("change", function() {
    const list = document.getElementById("threadFilesPreviewList");
    const MAX = 10;
    const files = Array.from(this.files).slice(0, MAX);
    list.innerHTML = "";
    if (!files.length) { list.classList.add("d-none"); return; }
    files.forEach(file => {
      const item = document.createElement("div");
      item.className = "list-group-item py-1 px-2 d-flex align-items-center gap-2 border-0";
      item.innerHTML = `<i class="bi ${fileIcon(file.name)} text-primary"></i>
        <span class="small text-truncate flex-grow-1">${escHtml(file.name)}</span>
        <span class="text-muted" style="font-size:.7rem">${formatFileSize(file.size)}</span>`;
      list.appendChild(item);
    });
    list.classList.remove("d-none");
  });

  // Save thread
  document.getElementById("btnThreadSave")?.addEventListener("click", async () => {
    const errEl   = document.getElementById("threadFormError");
    const threadId = document.getElementById("threadModalId").value;
    const text  = document.getElementById("threadText").value.trim();
    const topic = document.getElementById("threadTopic").value.trim();
    const imgInput  = document.getElementById("threadImages");
    const fileInput  = document.getElementById("threadFiles");

    if (!text) {
      errEl.textContent = "Text wajib diisi.";
      errEl.classList.remove("d-none");
      return;
    }

    const MAX_IMAGES = 20;
    const MAX_FILES  = 10;
    const selectedFiles     = imgInput  ? Array.from(imgInput.files).slice(0, MAX_IMAGES) : [];
    const selectedFileFiles = fileInput ? Array.from(fileInput.files).slice(0, MAX_FILES)  : [];

    const spinner = document.getElementById("threadSaveSpinner");
    const saveBtn  = document.getElementById("btnThreadSave");
    spinner?.classList.remove("d-none");
    if (saveBtn) saveBtn.disabled = true;

    const fd = new FormData();
    fd.append("text", text);
    fd.append("topic", topic);
    selectedFiles.forEach(f => fd.append("images", f));
    selectedFileFiles.forEach(f => fd.append("files", f));

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
      document.getElementById("threadImagesPreviewWrap").classList.add("d-none");
      document.getElementById("threadImagesPreviewGrid").innerHTML = "";
      const fpList = document.getElementById("threadFilesPreviewList");
      if (fpList) { fpList.innerHTML = ""; fpList.classList.add("d-none"); }
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
      await copyToClipboard(data.share_url);
      showToast("Link disalin ke clipboard!");
      const countEl = btn.querySelector("span");
      if (countEl) countEl.textContent = parseInt(countEl.textContent || "0") + 1;
    } catch(e) { showToast(e.message, true); }
  };

  // Filter by topic (called from topic badge click)
  window.filterByTopic = function(topic) {
    _topic = topic;
    const input = document.getElementById("topicFilter");
    if (input) input.value = topic;
    loadThreads(true);
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

  // Mouse-wheel → horizontal scroll on image strips
  document.getElementById("threadList")?.addEventListener("wheel", function(e) {
    const strip = e.target.closest(".thread-img-scroll");
    if (!strip) return;
    e.preventDefault();
    strip.scrollLeft += e.deltaY !== 0 ? e.deltaY : e.deltaX;
  }, { passive: false });

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

function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  // Fallback for HTTP / non-secure context
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity  = "0";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    document.execCommand("copy");
  } finally {
    document.body.removeChild(ta);
  }
  return Promise.resolve();
}

window.copyShareUrl = async function(thread_id) {
  try {
    const data = await apiJSON("/api/share/thread", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ thread_id }),
    });
    await copyToClipboard(data.share_url);
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

function linkify(str) {
  return escHtml(str).replace(
    /https?:\/\/[^\s<>"]+/g,
    url => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`
  );
}

function formatFileSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileIcon(name) {
  if (!name) return "bi-file-earmark";
  const ext = name.split(".").pop().toLowerCase();
  const map = {
    pdf:  "bi-file-earmark-pdf",
    doc:  "bi-file-earmark-word", docx: "bi-file-earmark-word",
    xls:  "bi-file-earmark-excel", xlsx: "bi-file-earmark-excel",
    csv:  "bi-file-earmark-spreadsheet", tsv: "bi-file-earmark-spreadsheet",
    ppt:  "bi-file-earmark-ppt", pptx: "bi-file-earmark-ppt",
    zip:  "bi-file-earmark-zip", rar: "bi-file-earmark-zip",
    "7z": "bi-file-earmark-zip", tar: "bi-file-earmark-zip", gz: "bi-file-earmark-zip",
    txt:  "bi-file-earmark-text", md: "bi-file-earmark-text", log: "bi-file-earmark-text",
    json: "bi-file-earmark-code", xml: "bi-file-earmark-code",
    rtf:  "bi-file-earmark-richtext",
  };
  return map[ext] || "bi-file-earmark";
}

function buildFileAttachmentsHtml(fileItems) {
  if (!Array.isArray(fileItems) || !fileItems.length) return "";
  const items = fileItems.map(f =>
    `<a href="${f.url}" download="${escHtml(f.name)}" class="list-group-item list-group-item-action py-1 px-2 d-flex align-items-center gap-2 border-0" target="_blank">
      <i class="bi ${fileIcon(f.name)} text-primary"></i>
      <span class="small text-truncate flex-grow-1" style="max-width:220px">${escHtml(f.name)}</span>
      ${f.size ? `<span class="text-muted" style="font-size:.7rem;white-space:nowrap">${formatFileSize(f.size)}</span>` : ""}
      <i class="bi bi-download text-muted ms-1" style="font-size:.75rem"></i>
    </a>`
  ).join("");
  return `<div class="list-group list-group-flush border rounded mb-2" style="font-size:.85rem">${items}</div>`;
}

/* ─── Image Lightbox ────────────────────────────────────────────────────── */

(function () {
  let _lbUrls  = [];
  let _lbIndex = 0;

  function lbShow(urls, index) {
    _lbUrls  = urls;
    _lbIndex = index;
    _lbRender();
    document.getElementById("imgLightbox")?.classList.add("active");
    document.body.style.overflow = "hidden";
  }

  function lbHide() {
    document.getElementById("imgLightbox")?.classList.remove("active");
    document.body.style.overflow = "";
  }

  function _lbRender() {
    const img     = document.getElementById("lbImg");
    const counter = document.getElementById("lbCounter");
    const prev    = document.getElementById("lbPrev");
    const next    = document.getElementById("lbNext");
    if (!img) return;
    img.src = _lbUrls[_lbIndex] || "";
    if (counter) counter.textContent = _lbUrls.length > 1 ? `${_lbIndex + 1} / ${_lbUrls.length}` : "";
    if (prev) prev.disabled = (_lbIndex === 0);
    if (next) next.disabled = (_lbIndex === _lbUrls.length - 1);
    // Hide nav buttons when only one image
    const showNav = _lbUrls.length > 1;
    if (prev) prev.style.display = showNav ? "" : "none";
    if (next) next.style.display = showNav ? "" : "none";
  }

  document.addEventListener("DOMContentLoaded", () => {
    const lb   = document.getElementById("imgLightbox");
    const prev = document.getElementById("lbPrev");
    const next = document.getElementById("lbNext");

    document.getElementById("lbClose")?.addEventListener("click", lbHide);

    // Click backdrop (not image or nav) to close
    lb?.addEventListener("click", (e) => {
      if (e.target === lb) lbHide();
    });

    prev?.addEventListener("click", () => {
      if (_lbIndex > 0) { _lbIndex--; _lbRender(); }
    });
    next?.addEventListener("click", () => {
      if (_lbIndex < _lbUrls.length - 1) { _lbIndex++; _lbRender(); }
    });

    // Keyboard navigation
    document.addEventListener("keydown", (e) => {
      if (!lb?.classList.contains("active")) return;
      if (e.key === "Escape")      lbHide();
      if (e.key === "ArrowLeft"  && _lbIndex > 0)                      { _lbIndex--; _lbRender(); }
      if (e.key === "ArrowRight" && _lbIndex < _lbUrls.length - 1)    { _lbIndex++; _lbRender(); }
    });

    // Touch/swipe support
    let _touchX = null;
    lb?.addEventListener("touchstart", (e) => { _touchX = e.touches[0].clientX; }, { passive: true });
    lb?.addEventListener("touchend", (e) => {
      if (_touchX === null) return;
      const dx = e.changedTouches[0].clientX - _touchX;
      _touchX = null;
      if (Math.abs(dx) < 40) return;
      if (dx < 0 && _lbIndex < _lbUrls.length - 1) { _lbIndex++; _lbRender(); }
      if (dx > 0 && _lbIndex > 0)                   { _lbIndex--; _lbRender(); }
    }, { passive: true });

    // Delegate: click on any .thread-img-thumb opens lightbox
    document.addEventListener("click", (e) => {
      const thumb = e.target.closest(".thread-img-thumb");
      if (!thumb) return;
      const strip = thumb.closest(".thread-img-scroll");
      const imgs  = strip
        ? Array.from(strip.querySelectorAll(".thread-img-thumb")).map(i => i.src)
        : [thumb.src];
      const idx   = strip
        ? Array.from(strip.querySelectorAll(".thread-img-thumb")).indexOf(thumb)
        : 0;
      lbShow(imgs, idx);
    });
  });
})();
