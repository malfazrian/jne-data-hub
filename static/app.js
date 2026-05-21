function normalizeName(name) {
    return String(name).replace(/\u200b/g, '').replace(/\xa0/g, '').trim();
}

const STORAGE_KEY_REQ = `${window.mode || "normal"}_last_request_id`;
const STORAGE_KEY_LIST = `project_lists_${mode}`;

let projects = window.projects || {};
let projectReferenceMtime = window.projectReferenceMtime || null;
let projectReferenceRefreshPromise = null;
let projectList = JSON.parse(localStorage.getItem(STORAGE_KEY_LIST)) || [];

const $accountChecklistWrapper = $('#account_checklist');
const $accountChecklistDiv = $('#account_checklist div');
const $addButton = $('#add_to_list');
const $tableBody = $('#project_list_body');
const $lateReasonCheckbox = $('#late_reason');
const $projectInput = $('#project_input');
const $projectSuggestions = $('#project_suggestions');
const $runBtn = $('#run_button');
const $downloadBtn = $('#download_button');
const $notifArea = $('#notif_area');
const bar = document.getElementById("progress-bar");

async function refreshProjectReference(force = false) {
  if (projectReferenceRefreshPromise) return projectReferenceRefreshPromise;

  projectReferenceRefreshPromise = fetch("/project_reference", { cache: "no-store" })
    .then(async (resp) => {
      if (!resp.ok) throw new Error("Gagal memuat referensi project");
      const data = await resp.json();
      if (force || data.mtime !== projectReferenceMtime) {
        projects = data.projects || {};
        window.projects = projects;
        projectReferenceMtime = data.mtime || null;
      }
    })
    .catch((err) => console.warn("Refresh project reference gagal:", err))
    .finally(() => {
      projectReferenceRefreshPromise = null;
    });

  return projectReferenceRefreshPromise;
}

function ensureLeadingApostrophe(id) {
    const s = String(id).trim();
    return s.startsWith("'") ? s : ("'" + s);
}

function renderTable() {
    $tableBody.empty();
    projectList.forEach((p, idx) => {
        const customersJoined = (p._customers || []).join('<br>');
        const $tr = $(`
            <tr>
                <td>${p.name}</td>
                <td>${p.id_account.join('<br>')}</td>
                <td>${customersJoined}</td>
                <td>${p.late_delivery ? 'Yes' : 'No'}</td>
                <td>
                    <button 
                        class="btn btn-sm me-1 edit-btn"
                        onclick="editProject(${idx})">
                        <i class="bi bi-pencil-square me-1"></i>Edit
                    </button>
                    <button 
                        class="btn btn-sm delete-btn"
                        onclick="deleteProject(${idx})">
                        <i class="bi bi-trash-fill me-1"></i>Hapus
                    </button>
                </td>
            </tr>
        `);
        $tableBody.append($tr);
    });

    const payload = projectList.map(({category, name, id_account, late_delivery}) => ({
        category, name, id_account, late_delivery
    }));
    localStorage.setItem(STORAGE_KEY_LIST, JSON.stringify(projectList));
    $('#project_lists_input').val(JSON.stringify(payload));
    $runBtn.prop('disabled', projectList.length === 0);
    const hasProjects = projectList.length > 0;
    $('#project_dependent_section').toggle(hasProjects);
}

function filterProjects(query) {
    query = normalizeName(query);
    if (!query) return [];
    return Object.keys(projects).filter(name =>
        normalizeName(name).toLowerCase().includes(query.toLowerCase())
    ).slice(0, 20);
}

// === AUTOCOMPLETE PROJECT ===

// === Search Mode Toggle ===
const $searchByName = $('#search_by_name');
const $searchById = $('#search_by_id');
const $searchLabel = $('#search_label');
let searchMode = 'name';

$('input[name="search_mode"]').on('change', function() {
  searchMode = this.value;
  if (searchMode === 'id') {
    $projectInput.attr('placeholder', 'Ketik ID Account...');
    $searchLabel.text('Cari ID Account:');
  } else {
    $projectInput.attr('placeholder', 'Ketik nama project...');
    $searchLabel.text('Pilih Project:');
  }
  $projectInput.val('');
  $projectSuggestions.empty().hide();
  $accountChecklistWrapper.hide();
  $('#unselect_all_accounts').hide();
});

function filterProjectsById(query) {
  query = normalizeName(query);
  if (!query) return [];
  // Cari di semua project, return array nama project yang punya id_account cocok
  const results = [];
  Object.entries(projects).forEach(([name, data]) => {
    if (Array.isArray(data.id_account)) {
      for (const pair of data.id_account) {
        if (String(pair.id).replace(/^'/, '').toLowerCase().includes(query.toLowerCase())) {
          results.push({name, id: pair.id, customer: pair.customer});
          break;
        }
      }
    }
  });
  return results.slice(0, 20);
}

$projectInput.on('focus', () => {
  refreshProjectReference();
});

$projectInput.on('input', async () => {
  await refreshProjectReference();
  const val = $projectInput.val();
  $projectSuggestions.empty();
  if (!val.trim()) {
    $projectSuggestions.hide();
    return;
  }

  let matches = [];
  if (searchMode === 'name') {
    matches = filterProjects(val).map(name => ({name}));
  } else {
    matches = filterProjectsById(val);
  }

  if (matches.length === 0) {
    $projectSuggestions.hide();
    return;
  }

  if (!$projectSuggestions.parent().is('body')) {
    $('body').append($projectSuggestions);
  }
  const offset = $projectInput.offset();
  const height = $projectInput.outerHeight();
  const width = $projectInput.outerWidth();
  $projectSuggestions.css({
    position: 'absolute',
    top: offset.top + height,
    left: offset.left,
    width: width,
    zIndex: 3000
  });

  if (searchMode === 'name') {
    matches.forEach(obj => {
      const $item = $(`<button type="button" class="list-group-item list-group-item-action">${obj.name}</button>`);
      $item.on('click', () => {
        $projectInput.val(obj.name).trigger('change');
        $projectSuggestions.empty().hide();
      });
      $projectSuggestions.append($item);
    });
  } else {
    matches.forEach(obj => {
      const $item = $(`<button type="button" class="list-group-item list-group-item-action">${obj.id} - ${obj.customer} <span class='text-muted small ms-2'>(${obj.name})</span></button>`);
      $item.on('click', () => {
        $projectInput.val(obj.name).trigger('change');
        $projectSuggestions.empty().hide();
      });
      $projectSuggestions.append($item);
    });
  }
  $projectSuggestions.show();
});

// Sembunyikan setelah blur
$projectInput.on('blur', () => {
    setTimeout(() => $projectSuggestions.hide(), 200);
});

function getSelectedProjectName() {
    return normalizeName($projectInput.val());
}

// Patch event handler
$projectInput.on('change', () => {
    const selected = getSelectedProjectName();
    if (!selected || !projects[selected]) {
        $accountChecklistWrapper.hide();
        $accountChecklistDiv.empty();
        return;
    }

    $accountChecklistDiv.empty();
    (projects[selected].id_account || []).forEach(pair => {
        const idWithTick = ensureLeadingApostrophe(pair.id);
        $accountChecklistDiv.append(`
            <div class="form-check">
                <input class="form-check-input" type="checkbox" value="${idWithTick}" checked>
                <label class="form-check-label">${idWithTick} - ${pair.customer || ''}</label>
            </div>
        `);
    });
    $accountChecklistWrapper.show();
    $('#unselect_all_accounts').show();
});

// Unselect All
$('#unselect_all_accounts').on('click', () => {
    $accountChecklistDiv.find('input[type="checkbox"]').prop('checked', false);
});

// Add ke list
$addButton.on('click', () => {
    const selected = getSelectedProjectName();
    if (!selected) return alert('Pilih project dulu!');
    const base = projects[selected];
    if (!base) return alert('Data project tidak ditemukan!');

    const checked = $accountChecklistDiv.find('input:checked');
    if (checked.length === 0) return alert('Pilih minimal 1 account!');

    const ids = checked.map((i, el) => ensureLeadingApostrophe($(el).val())).get();
    const custs = checked.map((i, el) => {
        const txt = $(el).next('label').text() || '';
        const parts = txt.split(' - ');
        return parts.length > 1 ? parts[1].trim() : '';
    }).get();

    const lateDelivery = $lateReasonCheckbox.is(':checked');

    const signature = selected + '|' + ids.sort().join(',');
    if (projectList.some(p => p._sig === signature)) {
        if (!confirm('Project yang sama sudah ada di daftar. Tambah duplikat?')) return;
    }

    projectList.push({
        category: base.category,
        name: selected,
        id_account: ids,
        late_delivery: lateDelivery,
        _customers: custs,
        _sig: signature
    });

    renderTable();
    $projectInput.val('');
    $accountChecklistDiv.empty();
    $accountChecklistWrapper.hide();
    $('#unselect_all_accounts').hide();
    $lateReasonCheckbox.prop('checked', false);
});

// Hapus
function deleteProject(idx) {
    projectList.splice(idx, 1);
    renderTable();
}

// Edit
function editProject(idx) {
    const proj = projectList[idx];
    $projectInput.val(proj.name);
    $lateReasonCheckbox.prop('checked', proj.late_delivery);

    const projectRef = projects[normalizeName(proj.name)];
    if (!projectRef) return;

    $accountChecklistDiv.empty();
    (projectRef.id_account || []).forEach(pair => {
        const idWithTick = ensureLeadingApostrophe(pair.id);
        const checked = proj.id_account.includes(idWithTick) ? 'checked' : '';
        $accountChecklistDiv.append(`
            <div class="form-check">
                <input class="form-check-input" type="checkbox" value="${idWithTick}" ${checked}>
                <label class="form-check-label">${idWithTick} - ${pair.customer || ''}</label>
            </div>
        `);
    });
    $accountChecklistWrapper.show();
    $('#unselect_all_accounts').show();

    // --- Open accordion Tambah Project jika belum terbuka ---
    const collapseEl = document.getElementById('collapseProject');
    if (!collapseEl.classList.contains("show")) {
        new bootstrap.Collapse(collapseEl, { show: true });
    }

    // --- Change accordion header text to Edit Project ---
    const $accordionBtn = $("#headingProject .accordion-button");
    $accordionBtn.html('<i class="bi bi-pencil-square me-2"></i>Edit Project');

    projectList.splice(idx, 1);
    renderTable();
}

// Reset accordion header back to Tambah Project after add
$("#add_to_list").on("click", function() {
    $("#headingProject .accordion-button")
      .html('<i class="bi bi-plus-circle me-2"></i>Tambah Project');
});

renderTable();
if (projectList.length === 0) {
    bootstrap.Collapse.getOrCreateInstance(document.getElementById('collapseProject')).show();
}

// Run process
let lastRequestId = localStorage.getItem(STORAGE_KEY_REQ);

// 🔹 Tentukan endpoint sesuai mode
let runUrl;
if (window.mode === "full") {
  runUrl = "/run_full";
} else if (window.mode === "report") {
  runUrl = "/run_report";
} else {
  runUrl = "/run";
}

let activeDataSource = "parquet";
const $sourceLabel = document.getElementById("selected_source_label");
if ($sourceLabel) {
  $sourceLabel.textContent = "Parquet";
}
document.querySelectorAll('#sourceSubNav .nav-link').forEach((btn) => {
  btn.addEventListener('shown.bs.tab', (e) => {
    const src = e.target.getAttribute('data-source');
    activeDataSource = src || "parquet";
    if ($sourceLabel) {
      $sourceLabel.textContent = activeDataSource === "csv" ? "CSV" : "Parquet";
    }
  });
});

// 🆕 Jika ada request_id tersimpan, cek statusnya untuk resume
if (lastRequestId) {
    checkAndResumeJob(lastRequestId);
}

async function checkAndResumeJob(reqId) {
  try {
    const res = await fetch(`/progress_status/${reqId}`);
    if (!res.ok) throw new Error("Gagal ambil status progress");

    const data = await res.json();

    if (data.done) {
      // ✅ Kalau proses sebelumnya sudah selesai
      $notifArea.html(
        `<div class="alert alert-success">
          Proses sebelumnya telah selesai. Silakan download hasilnya.
        </div>`
      );
      $downloadBtn.show();
      $runBtn.prop('disabled', false).text('Run');
      bar.style.width = "100%";
      bar.innerText = "100%";
    } else {
      // 🔄 Kalau masih berjalan, lanjutkan progress
      $notifArea.html(
        `<div class="alert alert-info">
          🔄 Melanjutkan progress sebelumnya...
        </div>`
      );
      $runBtn.prop('disabled', true).html(
        `<span class="spinner-border spinner-border-sm me-2"></span>Processing...`
      );
      $downloadBtn.hide();
      startProgress(reqId, true);
    }
  } catch (err) {
    console.warn("Resume gagal:", err);
    localStorage.removeItem(STORAGE_KEY_REQ);
    $notifArea.html(
      `<div class="alert alert-warning">
        Tidak dapat melanjutkan progress sebelumnya.
      </div>`
    );
    $runBtn.prop('disabled', false).text('Run');
  }
}

function getActiveDataSource() {
  const activeTab = document.querySelector('#sourceSubNav .nav-link.active');
  if (activeTab && activeTab.getAttribute('data-source')) {
    return activeTab.getAttribute('data-source');
  }
  return activeDataSource || 'parquet';
}

$runBtn.on('click', async () => {
  $runBtn.prop('disabled', true).html(`
    <span class="spinner-border spinner-border-sm me-2"></span> Processing...
  `);
  $notifArea.empty();

  const rangeValue = $('#date_range').val();

  if (!rangeValue) {
    $notifArea.html(`<div class="alert alert-danger">⚠️ Pilih range tanggal terlebih dahulu.</div>`);
    $runBtn.prop('disabled', false).html('<i class="bi bi-play-fill me-1"></i> Run');
    return;
  }

  const parseDateStr = (value) => {
    const match = String(value).trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return null;
    const y = Number(match[1]);
    const m = Number(match[2]);
    const d = Number(match[3]);
    const date = new Date(`${match[1]}-${match[2]}-${match[3]}T00:00:00`);
    if (Number.isNaN(date.getTime())) return null;
    if (date.getFullYear() !== y || date.getMonth() + 1 !== m || date.getDate() !== d) return null;
    return date;
  };

  const parseRange = (value) => {
    let parts = String(value).split(' - ');
    if (parts.length !== 2) {
      parts = String(value).split(' to ');
    }
    if (parts.length !== 2) return null;
    const start = parseDateStr(parts[0]);
    const end = parseDateStr(parts[1]);
    if (!start || !end) return null;
    return { start, end };
  };

  const toYYMMFromDate = (date) => {
    const year = String(date.getFullYear());
    const month = String(date.getMonth() + 1).padStart(2, "0");
    return year.slice(-2) + month;
  };

  const toYMD = (date) => {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };

  const range = parseRange(rangeValue);
  if (!range) {
    $notifArea.html(`<div class="alert alert-danger">⚠️ Format range tidak valid. Gunakan YYYY-MM-DD - YYYY-MM-DD.</div>`);
    $runBtn.prop('disabled', false).html('<i class="bi bi-play-fill me-1"></i> Run');
    return;
  }

  if (range.start > range.end) {
    $notifArea.html(`<div class="alert alert-danger">⚠️ Tanggal awal tidak boleh lebih baru dari tanggal akhir.</div>`);
    $runBtn.prop('disabled', false).html('<i class="bi bi-play-fill me-1"></i> Run');
    return;
  }

  // Ambil kategori (jika ada)
  let selectedCategories = [];
  const $categorySelect = $('#category_select');
  if ($categorySelect.length > 0) {
    selectedCategories = Array.from($categorySelect[0].selectedOptions).map(opt => opt.value);
    console.log("✅ Selected categories:", selectedCategories);
  }

  const payload = {
    start: toYYMMFromDate(range.start),
    end: toYYMMFromDate(range.end),
    start_date: toYMD(range.start),
    end_date: toYMD(range.end),
    project_lists_json: $('#project_lists_input').val(),
    project_status: $('#project_status_input').val() || "",
    filter_categories: selectedCategories,
    data_source: getActiveDataSource()
  };

  try {
    const resp = await fetch(runUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await resp.json();

    if (data.success) {
      $notifArea.html(`<div class="alert alert-success"><i class="bi bi-check-circle-fill me-1"></i>${data.message}</div>`);
      lastRequestId = data.request_id;
      localStorage.setItem(STORAGE_KEY_REQ, lastRequestId);

      // Reset dan tampilkan progress
      bar.style.width = "0%";
      bar.innerText = "0%";
      bar.classList.add("progress-bar-animated");

      startProgress(lastRequestId);
      $downloadBtn.hide();
    } else {
      $notifArea.html(`<div class="alert alert-danger"><i class="bi bi-x-circle-fill me-1"></i>${data.message}</div>`);
      $runBtn.prop('disabled', false).html('<i class="bi bi-play-fill me-1"></i> Run');
    }
  } catch (err) {
    $notifArea.html(`<div class="alert alert-danger"><i class="bi bi-exclamation-triangle-fill me-1"></i>Error: ${err}</div>`);
    $runBtn.prop('disabled', false).html('<i class="bi bi-play-fill me-1"></i> Run');
  }
});

// === UX: tampilkan badge kategori terpilih ===
$('#category_select').on('change', function () {
  const selected = Array.from(this.selectedOptions).map(opt => opt.value);
  const $badgeArea = $('#selected_categories_badge');
  $badgeArea.empty();

  if (selected.length === 0) {
    $badgeArea.html('<span class="text-muted small fst-italic">No category selected</span>');
  } else {
    selected.forEach(cat => {
      $badgeArea.append(`<span class="badge bg-secondary">${cat}</span>`);
    });
  }
});

$downloadBtn.on('click', () => {
    if (!lastRequestId) {
        $notifArea.html(`<div class="alert alert-danger">Request ID tidak ditemukan!</div>`);
        return;
    }
    window.location.href = `/download/${lastRequestId}`;
    $runBtn.show();
    $downloadBtn.hide();
    $notifArea.empty();
    bar.style.width = "0%";
    bar.innerText = "0%";
});

document.addEventListener("DOMContentLoaded", () => {
  const rangeInput = document.getElementById("date_range");
  if (!rangeInput) return;

  const formatDate = (date) => {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  };

  const today = new Date();
  const startPrevMonth = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const endPrevMonth = new Date(today.getFullYear(), today.getMonth(), 0);
  const defaultRange = `${formatDate(startPrevMonth)} - ${formatDate(endPrevMonth)}`;

  if (window.flatpickr) {
    window.flatpickr(rangeInput, {
      mode: "range",
      dateFormat: "Y-m-d",
      rangeSeparator: " - ",
      defaultDate: [startPrevMonth, endPrevMonth],
      allowInput: true
    });
  } else {
    rangeInput.value = defaultRange;
  }

  const categorySelect = document.getElementById("category_select");
  if (categorySelect && window.TomSelect) {
    new TomSelect(categorySelect, {
      plugins: ["remove_button"],
      maxItems: null,
      placeholder: "Pilih kategori...",
      hideSelected: true,
      closeAfterSelect: false
    });
  }
});

// Subtle gradient follow mouse
document.addEventListener("mousemove", (e) => {
  const x = `${(e.clientX / window.innerWidth) * 100}%`;
  const y = `${(e.clientY / window.innerHeight) * 100}%`;
  document.body.style.setProperty("--mouse-x", x);
  document.body.style.setProperty("--mouse-y", y);
});

const collapseUsage = document.getElementById("collapseUsage");

// Saat accordion dibuka
collapseUsage.addEventListener("shown.bs.collapse", () => {
// Tambah listener klik di luar
document.addEventListener("click", outsideClickListener);
});

// Saat accordion ditutup
collapseUsage.addEventListener("hidden.bs.collapse", () => {
document.removeEventListener("click", outsideClickListener);
});

function outsideClickListener(event) {
if (!collapseUsage.contains(event.target) &&
    !document.getElementById("headingUsage").contains(event.target)) {
    // Tutup accordion jika klik di luar
    const bsCollapse = bootstrap.Collapse.getInstance(collapseUsage);
    bsCollapse.hide();
}
}

document.getElementById("year").textContent = new Date().getFullYear();

// === Copy Project List ===
function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  // Fallback untuk HTTP / non-secure context (e.g. local IP)
  return new Promise((resolve, reject) => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error('execCommand failed'));
    } catch (e) {
      document.body.removeChild(ta);
      reject(e);
    }
  });
}

$('#copy_project_list_btn').on('click', () => {
  if (projectList.length === 0) {
    alert('Daftar project masih kosong!');
    return;
  }
  const copyData = projectList.map(({id_account, late_delivery}) => ({
    id_account, late_delivery
  }));
  copyTextToClipboard(JSON.stringify(copyData, null, 2)).then(() => {
    const $btn = $('#copy_project_list_btn');
    $btn.html('<i class="bi bi-clipboard-check"></i>');
    const $notif = $('#copy_notif');
    $notif.stop(true, true).show().delay(2000).fadeOut(400, () => {
      $btn.html('<i class="bi bi-clipboard"></i>');
    });
  }).catch(() => {
    alert('Gagal menyalin ke clipboard.');
  });
});

// === Import Project List (Bulk) ===
$('#save_import_project_btn').on('click', async () => {
  await refreshProjectReference();

  const raw = $('#import_project_textarea').val().trim();
  const $err = $('#import_error_msg');
  $err.hide();

  if (!raw) {
    $err.text('Input tidak boleh kosong.').show();
    return;
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    $err.text('JSON tidak valid: ' + e.message).show();
    return;
  }

  if (!Array.isArray(parsed) || parsed.length === 0) {
    $err.text('Input harus berupa array JSON yang tidak kosong.').show();
    return;
  }

  const invalid = parsed.find(p => !Array.isArray(p.id_account) || p.id_account.length === 0);
  if (invalid) {
    $err.text('Setiap item harus memiliki field "id_account" (array tidak kosong).').show();
    return;
  }

  // Build reverse lookup: normalized id -> project name
  const idToProjectName = {};
  Object.entries(projects).forEach(([name, data]) => {
    (data.id_account || []).forEach(pair => {
      idToProjectName[ensureLeadingApostrophe(pair.id)] = name;
    });
  });

  const failed = [];
  projectList = parsed.map(p => {
    const ids = (p.id_account || []).map(id => ensureLeadingApostrophe(id));
    const name = idToProjectName[ids[0]];
    if (!name || !projects[name]) {
      failed.push(ids[0]);
      return null;
    }
    const base = projects[name];
    const custs = ids.map(id => {
      const pair = (base.id_account || []).find(pair => ensureLeadingApostrophe(pair.id) === id);
      return pair ? (pair.customer || '') : '';
    });
    const signature = name + '|' + ids.slice().sort().join(',');
    return {
      category: base.category || '',
      name,
      id_account: ids,
      late_delivery: p.late_delivery || false,
      _customers: custs,
      _sig: signature
    };
  }).filter(Boolean);

  if (failed.length > 0) {
    $err.text('ID tidak ditemukan di referensi: ' + failed.join(', ')).show();
  }

  renderTable();
  bootstrap.Modal.getInstance(document.getElementById('importProjectModal')).hide();
  $('#import_project_textarea').val('');
});

function startProgress(requestId) {
  const evtSource = new EventSource(`/progress/${requestId}`);
  evtSource.onmessage = function(e) {
    const percent = parseInt(e.data);
    const bar = document.getElementById("progress-bar");
    bar.style.width = percent + "%";
    bar.innerText = percent + "%";

    // Progress bar update real-time
    if (percent >= 100) {
      evtSource.close();
      $downloadBtn.show();
      $runBtn.prop('disabled', projectList.length === 0).text('Run').hide();
      $notifArea.html(`<div class="alert alert-success">Proses selesai, file siap di-download.</div>`);
    } else if (!isResume) {
        localStorage.setItem(STORAGE_KEY_REQ, requestId);
    }
  };

  evtSource.onerror = function() {
        console.warn("SSE connection lost");
        evtSource.close();
    };
}
