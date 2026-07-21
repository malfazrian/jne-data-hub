(() => {
  function relativeTime(date) {
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return "baru saja";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} menit lalu`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} jam lalu`;
    return `${Math.floor(seconds / 86400)} hari lalu`;
  }

  function fullTime(date) {
    return new Intl.DateTimeFormat("id-ID", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  }

  async function hydrateChip(chip) {
    if (chip.dataset.freshnessInitialized === "true") return;
    chip.dataset.freshnessInitialized = "true";
    const text = chip.querySelector(".data-freshness-text");

    try {
      const source = chip.dataset.freshnessSource;
      const response = await fetch(`/data-freshness/${encodeURIComponent(source)}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      chip.className = `data-freshness-chip is-${data.status || "unknown"}`;

      if (!data.updated_at) {
        text.textContent = data.label || "Status tidak diketahui";
        chip.title = "Waktu pembaruan belum tersedia";
        return;
      }

      const updatedAt = new Date(data.updated_at);
      text.textContent = `${data.label} ${relativeTime(updatedAt)}`;
      chip.title = `Terakhir diperbarui: ${fullTime(updatedAt)}`;
    } catch (error) {
      chip.className = "data-freshness-chip is-unknown";
      text.textContent = "Status tidak diketahui";
      chip.title = "Status pembaruan tidak dapat dimuat";
    }
  }

  function initialize() {
    document.querySelectorAll("[data-freshness-source]").forEach(hydrateChip);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
