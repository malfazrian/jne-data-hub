
  // Subtle gradient follow mouse
  document.addEventListener("mousemove", (e) => {
    const x = `${(e.clientX / window.innerWidth) * 100}%`;
    const y = `${(e.clientY / window.innerHeight) * 100}%`;
    document.body.style.setProperty("--mouse-x", x);
    document.body.style.setProperty("--mouse-y", y);
  });

// === Inisialisasi date range picker ===
flatpickr("#dateRange", {
  mode: "range",
  dateFormat: "d-M-Y",
  locale: "id",
});

// === Cek apakah ada task tersimpan di localStorage ===
document.addEventListener("DOMContentLoaded", () => {
  const savedTaskId = localStorage.getItem("apex_task_id");
  const savedTaskData = localStorage.getItem("apex_task_data");

  if (savedTaskId && savedTaskData) {
    const parsed = JSON.parse(savedTaskData);
    document.getElementById("progressSection").classList.remove("d-none");

    // Disable form biar nggak bisa submit ulang
    const form = document.getElementById("apexForm");
    Array.from(form.elements).forEach((el) => (el.disabled = true));

    // Tampilkan status “melanjutkan proses sebelumnya...”
    const progressBar = document.getElementById("progressBar");
    progressBar.classList.add("progress-bar-animated");
    progressBar.textContent = "Melanjutkan progress sebelumnya...";

    startPolling(savedTaskId);
  }
});

// === Handle form submit ===
document.getElementById("apexForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const startBtn = document.getElementById("startBtn");
  const resetBtn = document.getElementById("resetBtn");
  const form = document.getElementById("apexForm");

  // Blokir form saat running
  startBtn.disabled = true;
  startBtn.textContent = "⏳ Sedang diproses...";
  Array.from(form.elements).forEach((el) => {
    if (el.id !== "resetBtn") el.disabled = true; // ❌ kecuali resetBtn
  });

  // 🆕 Tampilkan tombol reset segera
  resetBtn.classList.remove("d-none");
  resetBtn.onclick = async () => {
    const taskId = localStorage.getItem("apex_task_id");
    if (taskId) {
      await fetch(`/cancel-request/${taskId}`, { method: "POST" });
    }
    resetForm();
  };

  // Ambil data form & kirim request seperti biasa...
  const host = document.getElementById("host").value;
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  const dateRange = document.getElementById("dateRange").value.split(" to ");
  const tanggal_awal = dateRange[0];
  const tanggal_akhir = dateRange[1] || tanggal_awal;
  const customer_ids = document
    .getElementById("customer_ids")
    .value
    .split(/[\s,]+/)
    .filter((id) => id.trim() !== "")
    .map((id) => ({ customer_id: id.trim() }));

  const response = await fetch("/start-request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      host,
      username,
      password,
      tanggal_awal,
      tanggal_akhir,
      list_customer_ids: customer_ids,
    }),
  });

  const data = await response.json();
  const taskId = data.task_id;

  localStorage.setItem("apex_task_id", taskId);
  localStorage.setItem(
    "apex_task_data",
    JSON.stringify({
      host,
      username,
      tanggal_awal,
      tanggal_akhir,
      list_customer_ids: customer_ids,
    })
  );

  document.getElementById("progressSection").classList.remove("d-none");
  startPolling(taskId);
});

// === Polling progress ===
async function startPolling(taskId) {
  const progressBar = document.getElementById("progressBar");
  const trackerBody = document.querySelector("#trackerTable tbody");
  const startBtn = document.getElementById("startBtn");
  const resetBtn = document.getElementById("resetBtn");

  const interval = setInterval(async () => {
    const res = await fetch(`/progress-request/${taskId}`);
    const data = await res.json();

    if (data.status === "cancelled") {
      clearInterval(interval);
      progressBar.classList.remove("progress-bar-animated");
      progressBar.classList.add("bg-secondary");
      progressBar.textContent = "Dibatalkan";

      localStorage.removeItem("apex_task_id");
      localStorage.removeItem("apex_task_data");

      resetBtn.classList.remove("d-none");
    }

    if (data.error) {
      clearInterval(interval);
      alert(data.error);

      // 🧹 Bersihkan localStorage dan reset form agar bisa mulai baru
      localStorage.removeItem("apex_task_id");
      localStorage.removeItem("apex_task_data");
      resetForm();

      return;
    }


    progressBar.style.width = data.progress + "%";
    progressBar.textContent = data.progress + "%";

    trackerBody.innerHTML = "";
    data.tracker.forEach((t) => {
      // Tentukan ikon status
      const reqIcon = t.request
        ? "✅"
        : t.reason
        ? "❌"
        : "⏳"; // belum diproses

      const dlIcon = t.download
        ? "✅"
        : t.reason
        ? "❌"
        : "⏳";

      // Tooltip hanya kalau ada reason
      const reasonTooltip = t.reason
        ? `title="${t.reason.replace(/"/g, "&quot;")}"`
        : "";

      trackerBody.innerHTML += `
        <tr>
          <td>${t.task}</td>
          <td ${reasonTooltip} data-bs-toggle="tooltip" data-bs-placement="top" class="text-center">${reqIcon}</td>
          <td ${reasonTooltip} data-bs-toggle="tooltip" data-bs-placement="top" class="text-center">${dlIcon}</td>
        </tr>
      `;
    });

    // Bersihkan tooltip lama
    document.querySelectorAll('.tooltip').forEach(el => el.remove());

    // Inisialisasi ulang tooltip yang baru
    const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggerList.forEach(el => {
      // Kalau sebelumnya sudah punya instance, dispose dulu
      const existingTooltip = bootstrap.Tooltip.getInstance(el);
      if (existingTooltip) existingTooltip.dispose();

      // Buat instance baru
      new bootstrap.Tooltip(el, {
        trigger: 'hover',
        container: 'body',
      });
    });

    if (data.status === "finished") {
      clearInterval(interval);
      progressBar.classList.remove("progress-bar-animated");
      progressBar.classList.add("bg-success");

      // 🧹 Hapus data dari localStorage karena sudah selesai
      localStorage.removeItem("apex_task_id");
      localStorage.removeItem("apex_task_data");

      const startBtn = document.getElementById("startBtn");
      const resetBtn = document.getElementById("resetBtn");

        startBtn.type = "button";
        startBtn.disabled = false;
        startBtn.classList.replace("btn-primary", "btn-success");
        startBtn.innerHTML = `⬇️ Download Hasil`;
        startBtn.onclick = () => {
        window.location.href = `/download-request/${taskId}`;
        };

      // Tampilkan tombol reset
      resetBtn.classList.remove("d-none");
      resetBtn.onclick = async () => {
        const taskId = localStorage.getItem("apex_task_id");

        if (taskId) {
          await fetch(`/cancel-request/${taskId}`, { method: "POST" });
        }

        resetForm();
      };
    }
  }, 2000);
}

// === Reset form untuk task baru ===
function resetForm() {
  const form = document.getElementById("apexForm");
  form.reset();
  document.getElementById("progressSection").classList.add("d-none");

  const startBtn = document.getElementById("startBtn");
  startBtn.disabled = false;
  startBtn.textContent = "Mulai Proses";
  startBtn.classList.remove("btn-success");
  startBtn.classList.add("btn-primary");
  startBtn.onclick = null;

  document.getElementById("resetBtn").classList.add("d-none");
  Array.from(form.elements).forEach((el) => (el.disabled = false));

  // Bersihkan localStorage
  localStorage.removeItem("apex_task_id");
  localStorage.removeItem("apex_task_data");

  // 🔥 Re-init flatpickr karena form di-reset dan sebelumnya di-clone
  flatpickr("#dateRange", {
    mode: "range",
    dateFormat: "d-M-Y",
    locale: "id",
  });
}