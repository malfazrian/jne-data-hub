/**
 * thread_detail.js
 * Logic specific to the thread detail page (/thread/<id>).
 */

const CFG_D = window.THREADS_CONFIG || {};

document.addEventListener("DOMContentLoaded", () => {

  // Sync window._currentUser from server-rendered user data if not already set
  if (!window._currentUser && CFG_D.user) {
    window._currentUser = CFG_D.user;
  }

  /* ── Comment image preview ───────────────────────────────────────────── */
  const commentImgInput = document.getElementById("commentImage");
  const commentImgPreview = document.getElementById("commentImagePreview");
  const commentImgEl    = document.getElementById("commentImgPreviewEl");

  commentImgInput?.addEventListener("change", function() {
    if (this.files[0]) {
      commentImgEl.src = URL.createObjectURL(this.files[0]);
      commentImgPreview.classList.remove("d-none");
    }
  });

  /* ── Comment form submit ─────────────────────────────────────────────── */
  document.getElementById("commentForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!window._currentUser) {
      openProfileModal(() => document.getElementById("commentForm").requestSubmit());
      return;
    }
    const form   = e.target;
    const errEl  = document.getElementById("commentFormError");
    const fd     = new FormData(form);

    errEl.classList.add("d-none");

    try {
      const data = await apiJSON("/api/comment", { method: "POST", body: fd });
      appendComment(data, fd.get("parent_comment_id") || "");
      form.reset();
      commentImgPreview?.classList.add("d-none");
      cancelReply();
      showToast("Komentar diposting!");
      document.getElementById("noComments")?.remove();
    } catch(e) {
      errEl.textContent = e.message;
      errEl.classList.remove("d-none");
    }
  });

  /* ── Edit thread (detail page) ───────────────────────────────────────── */
  const editThreadModal = document.getElementById("editThreadModal");
  if (editThreadModal) {
    document.getElementById("editThreadText").value  = CFG_D.threadText  || "";
    document.getElementById("editThreadTopic").value = CFG_D.threadTopic || "";
    document.getElementById("btnEditThreadSave")?.addEventListener("click", async () => {
      const form = document.getElementById("editThreadForm");
      const fd   = new FormData(form);
      try {
        await apiJSON(`/api/hub/${CFG_D.threadId}`, { method: "PUT", body: fd });
        showToast("Thread diperbarui. Muat ulang untuk melihat perubahan.");
        bootstrap.Modal.getInstance(editThreadModal)?.hide();
      } catch(e) { showToast(e.message, true); }
    });
  }

  /* ── Profile modal ───────────────────────────────────────────────────── */
  document.getElementById("btnProfileSave")?.addEventListener("click", async () => {
    const form    = document.getElementById("profileForm");
    const errEl   = document.getElementById("profileFormError");
    const username = (document.getElementById("profileUsername")?.value || "").trim();
    if (!username) {
      if (errEl) { errEl.textContent = "Username wajib diisi."; errEl.classList.remove("d-none"); }
      return;
    }
    try {
      const fd   = new FormData(form);
      const data = await apiJSON("/api/user/update", { method: "POST", body: fd });
      window._currentUser = { username: data.username, profile_pic: data.profile_pic };
      bootstrap.Modal.getInstance(document.getElementById("profileModal"))?.hide();
      showToast("Profil tersimpan!");
    } catch(e) {
      if (errEl) { errEl.textContent = e.message; errEl.classList.remove("d-none"); }
    }
  });

  document.getElementById("btnProfileCancel")?.addEventListener("click", () => {
    bootstrap.Modal.getInstance(document.getElementById("profileModal"))?.hide();
  });
});

/* ── Helper: append new comment to DOM ───────────────────────────────────── */

function appendComment(c, parentId) {
  const owned   = (c.user_ip === CFG_D.currentIp);
  const menuHtml = owned ? `
    <div class="dropdown">
      <button class="btn btn-link btn-sm p-0 text-muted" data-bs-toggle="dropdown"><i class="bi bi-three-dots"></i></button>
      <ul class="dropdown-menu dropdown-menu-end">
        <li><button class="dropdown-item small" onclick="openEditComment('${c.comment_id}', this)"><i class="bi bi-pencil me-1"></i>Edit</button></li>
        <li><button class="dropdown-item small text-danger" onclick="deleteComment('${c.comment_id}')"><i class="bi bi-trash me-1"></i>Hapus</button></li>
      </ul>
    </div>` : "";
  const imgHtml = c.image_url
    ? `<img src="${c.image_url}" class="img-fluid rounded mb-1" style="max-height:150px;" alt="image">`
    : "";

  const div = document.createElement("div");
  div.className = "thread-card mb-2 comment-item new-item";
  div.id = `cmt-${c.comment_id}`;
  div.dataset.id = c.comment_id;
  const isReply  = !!parentId;
  const picUrl   = (c.user_ip === CFG_D.currentIp && window._currentUser?.profile_pic)
                    ? window._currentUser.profile_pic : "";
  const avatarHtmlStr = picUrl
    ? `<img src="${picUrl}" class="${isReply ? 'thread-avatar-xs' : 'thread-avatar-sm'}" alt="avatar" onerror="this.style.display='none'">`
    : `<div class="${isReply ? 'thread-avatar-xs-placeholder' : 'thread-avatar-sm-placeholder'}">${(c.username_snapshot||"?")[0].toUpperCase()}</div>`;

  div.innerHTML = `
    <div class="d-flex gap-2">
      <div class="flex-shrink-0">${avatarHtmlStr}</div>
      <div class="flex-grow-1">
        <div class="d-flex justify-content-between">
          <div>
            <span class="fw-semibold small">${escHtml(c.username_snapshot)}</span>
            <span class="text-muted ms-2" style="font-size:.75rem;" data-ts="${c.created_at}"></span>
          </div>
          ${menuHtml}
        </div>
        <p class="mb-1 mt-1 small comment-text-${c.comment_id}">${escHtml(c.text)}</p>
        ${imgHtml}
        <div class="d-flex gap-3 mt-1">
          <button class="btn btn-link btn-sm p-0 text-muted like-btn"
                  data-id="${c.comment_id}" data-kind="comment" onclick="toggleLike(this)">
            <i class="bi bi-heart"></i>
            <span class="like-count">0</span>
          </button>
          <button class="btn btn-link btn-sm p-0 text-muted"
                  onclick="setReply('${c.comment_id}', '${escHtml(c.username_snapshot)}')">
            <i class="bi bi-reply"></i> Balas
          </button>
        </div>
      </div>
    </div>`;

  if (parentId) {
    const parentCard = document.getElementById(`cmt-${parentId}`);
    if (parentCard) {
      // Increment reply count badge on the parent comment
      const badge = parentCard.querySelector(".reply-count-badge");
      if (badge) badge.textContent = parseInt(badge.textContent || "0") + 1;
      // Add to the dedicated replies container
      const replyContainer = document.getElementById(`replies-${parentId}`);
      if (replyContainer) {
        replyContainer.dataset.loaded = "1";
        replyContainer.classList.remove("d-none");
        replyContainer.appendChild(div);
      }
    } else {
      document.getElementById("commentList").appendChild(div);
    }
  } else {
    document.getElementById("commentList").appendChild(div);
  }
  renderRelTimes();
}

/* ── Lazy-load replies ───────────────────────────────────────────────────── */

window.toggleReplies = async function(commentId, btn) {
  const container = document.getElementById(`replies-${commentId}`);
  if (!container) return;

  // Already loaded — just toggle visibility
  if (container.dataset.loaded === "1") {
    container.classList.toggle("d-none");
    return;
  }

  btn.disabled = true;
  try {
    const replies = await apiJSON(`/api/comment/${commentId}/replies`);
    container.dataset.loaded = "1";
    replies.forEach(r => {
      const owned = (r.user_ip === CFG_D.currentIp);
      const menuHtml = owned ? `
        <div class="dropdown">
          <button class="btn btn-link btn-sm p-0 text-muted" data-bs-toggle="dropdown"><i class="bi bi-three-dots"></i></button>
          <ul class="dropdown-menu dropdown-menu-end">
            <li><button class="dropdown-item small" onclick="openEditComment('${r.comment_id}', this)"><i class="bi bi-pencil me-1"></i>Edit</button></li>
            <li><button class="dropdown-item small text-danger" onclick="deleteComment('${r.comment_id}')"><i class="bi bi-trash me-1"></i>Hapus</button></li>
          </ul>
        </div>` : "";
      const imgHtml = r.image_url
        ? `<img src="${r.image_url}" class="img-fluid rounded mb-1" style="max-height:150px;" alt="image">`
        : "";
      const avatarHtmlStr = r.avatar_url
        ? `<img src="${r.avatar_url}" class="thread-avatar-xs" alt="avatar" onerror="this.style.display='none'">`
        : `<div class="thread-avatar-xs-placeholder">${(r.username_snapshot||"?")[0].toUpperCase()}</div>`;
      const likedClass = r.liked ? "liked" : "";
      const heartIcon  = r.liked ? "bi-heart-fill text-danger" : "bi-heart";

      const div = document.createElement("div");
      div.className = "mb-2 comment-item";
      div.id = `cmt-${r.comment_id}`;
      div.dataset.id = r.comment_id;
      div.innerHTML = `
        <div class="d-flex gap-2">
          <div class="flex-shrink-0">${avatarHtmlStr}</div>
          <div class="flex-grow-1">
            <div class="d-flex justify-content-between">
              <span class="fw-semibold" style="font-size:0.8rem;">${escHtml(r.username_snapshot)}</span>
              ${menuHtml}
            </div>
            <p class="mb-1 mt-1 small comment-text-${r.comment_id}">${escHtml(r.text)}</p>
            ${imgHtml}
            <div class="d-flex gap-3 mt-1">
              <button class="btn btn-link btn-sm p-0 text-muted like-btn ${likedClass}"
                      data-id="${r.comment_id}" data-kind="comment" onclick="toggleLike(this)">
                <i class="bi ${heartIcon}"></i>
                <span class="like-count">${r.like_count}</span>
              </button>
            </div>
          </div>
        </div>`;
      container.appendChild(div);
    });
    container.classList.remove("d-none");
    renderRelTimes();
  } catch(e) {
    showToast(e.message, true);
  } finally {
    btn.disabled = false;
  }
};

/* ── Reply helper ────────────────────────────────────────────────────────── */

window.setReply = function(parentCommentId, username) {
  document.getElementById("parentCommentId").value = parentCommentId;
  const bar = document.getElementById("replyingToBar");
  bar.innerHTML = `<i class="bi bi-reply me-1"></i>Membalas <strong>${escHtml(username)}</strong>
    <button type="button" class="btn btn-link btn-sm p-0 text-muted ms-2" onclick="cancelReply()">Batal</button>`;
  bar.classList.remove("d-none");
  document.getElementById("commentText").focus();
};

window.cancelReply = function() {
  document.getElementById("parentCommentId").value = "";
  document.getElementById("replyingToBar").classList.add("d-none");
};

/* ── Clear comment image ─────────────────────────────────────────────────── */

window.clearCommentImage = function() {
  document.getElementById("commentImage").value = "";
  document.getElementById("commentImagePreview").classList.add("d-none");
};

/* ── Edit thread modal opener ────────────────────────────────────────────── */

window.openEditThread = function() {
  new bootstrap.Modal(document.getElementById("editThreadModal")).show();
};

/* ── Delete thread ───────────────────────────────────────────────────────── */

window.deleteThread = async function(thread_id) {
  if (!confirm("Hapus thread ini?")) return;
  try {
    await apiJSON(`/api/hub/${thread_id}`, { method: "DELETE" });
    showToast("Thread dihapus. Mengalihkan…");
    setTimeout(() => { window.location.href = "/hub"; }, 1500);
  } catch(e) { showToast(e.message, true); }
};

/* ── Delete comment ──────────────────────────────────────────────────────── */

window.deleteComment = async function(comment_id) {
  if (!confirm("Hapus komentar ini?")) return;
  try {
    await apiJSON(`/api/comment/${comment_id}`, { method: "DELETE" });
    document.getElementById(`cmt-${comment_id}`)?.remove();
    showToast("Komentar dihapus.");
  } catch(e) { showToast(e.message, true); }
};

/* ── Edit comment inline ─────────────────────────────────────────────────── */

window.openEditComment = function(comment_id, triggerBtn) {
  const textEl = document.querySelector(`.comment-text-${comment_id}`);
  if (!textEl) return;

  const originalText = textEl.textContent;
  const input = document.createElement("textarea");
  input.className = "form-control form-control-sm mb-1";
  input.rows = 2;
  input.value = originalText;

  const saveBtn = document.createElement("button");
  saveBtn.className = "btn btn-primary btn-sm me-1";
  saveBtn.textContent = "Simpan";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn btn-secondary btn-sm";
  cancelBtn.textContent = "Batal";

  cancelBtn.addEventListener("click", () => {
    textEl.replaceWith(createTextEl(comment_id, originalText));
    input.remove(); saveBtn.remove(); cancelBtn.remove();
  });

  saveBtn.addEventListener("click", async () => {
    const newText = input.value.trim();
    if (!newText) return;
    try {
      await apiJSON(`/api/comment/${comment_id}`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ text: newText }),
      });
      const newEl = createTextEl(comment_id, newText);
      textEl.replaceWith(newEl);
      input.remove(); saveBtn.remove(); cancelBtn.remove();
      showToast("Komentar diperbarui.");
    } catch(e) { showToast(e.message, true); }
  });

  textEl.replaceWith(input);
  input.after(saveBtn);
  saveBtn.after(cancelBtn);
  input.focus();
};

function createTextEl(comment_id, text) {
  const p = document.createElement("p");
  p.className = `mb-1 mt-1 small comment-text-${comment_id}`;
  p.textContent = text;
  return p;
}
