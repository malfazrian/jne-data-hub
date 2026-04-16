import time
import traceback
import os
from pathlib import Path
from selenium.webdriver.common.by import By
from .apex_ops import (
    start_driver, upload_all_csv_in_folder, click_download_link,
    wait_for_download, login_to_apex, klik_report_status_awb, find_latest_uploaded_file
)
from .file_ops import (
    get_csv_files, split_csv_file, ensure_folder, clear_folder_of_csvs,
    update_tracker, is_already_done, all_downloaded, reset_uploaded_status,
    rename_csv_file_with_suffix, merge_csv_files, get_base_filename,
    load_tracker
)
from .data_ops import extract_all_awbs_from_folder, save_awbs_to_csv


class ApexQueryJob:
    """Kelas untuk menjalankan proses query data ke server APEX."""

    def __init__(self, base_dir: Path, username: str, password: str, request_id=None, status_dict=None):
        """
        base_dir -> direktori utama untuk 1 job, misalnya: data/jobs/<job_id>
        """
        self.base_dir = Path(base_dir)
        self.username = username
        self.password = password
        self.request_id = request_id or f"req_{int(time.time())}"
        self.status_dict = status_dict

        # Struktur folder di dalam job
        self.source_folder = self.base_dir  / "source"
        self.upload_dir = self.base_dir  / "to_upload"
        self.download_dir = self.base_dir  / "downloads"
        self.tracker_dir = self.base_dir  / "tracker"

        # File tracker
        self.tracker_path = self.tracker_dir / "status_tracker.csv"

        # Konfigurasi umum
        self.chunk_size = 9999
        self.upload_retry_limit = 3
        self.hosts = [h.strip() for h in os.getenv("APEX_UPLOAD_HOSTS", "").split(",") if h.strip()]

        # Status runtime
        self.driver = None
        self.success = False

    # -----------------------------------------
    # Utility
    # -----------------------------------------
    def _setup_folders(self):
        for folder in [self.tracker_dir, self.upload_dir, self.download_dir.resolve()]:
            ensure_folder(folder)
            clear_folder_of_csvs(folder)

    def _log(self, msg):
        """Kirim log ke console dan Flask status dict jika tersedia."""
        print(f"[ApexQueryJob] {msg}")
        if self.status_dict and self.request_id in self.status_dict:
            self.status_dict[self.request_id]["log"].append(msg)
            # Update progress sederhana
            prog = self.status_dict[self.request_id]["progress"]
            self.status_dict[self.request_id]["progress"] = min(prog + 5, 95)

    def get_active_host(self):
        """Ambil host yang akan digunakan."""
        return self.selected_host or self.hosts[0]

    # -----------------------------------------
    # Core Logic
    # -----------------------------------------
    def extract_awb_data(self):
        self._log(f"Ekstraksi AWB dari file")
        # Ambil file CSV maupun Excel (.xlsx, .xls, .xlsm)
        source_files = list(self.source_folder.glob("*.csv")) + \
                      list(self.source_folder.glob("*.xlsx")) + \
                      list(self.source_folder.glob("*.xls")) + \
                      list(self.source_folder.glob("*.xlsm"))
        
        if not source_files:
            self._log("Tidak ada file ditemukan di source folder!")
            return False

        for source_file in source_files:
            awb_set = extract_all_awbs_from_folder(source_file.parent)
            if not awb_set:
                self._log(f"Tidak ada AWB ditemukan di {source_file.name}")
                continue

            # Simpan dengan nama file asli (ubah extension jadi .csv)
            output_csv = "Update" / self.download_dir / (source_file.stem + ".csv")
            save_awbs_to_csv(awb_set, output_path=output_csv)
            
            # Split dengan base_name dari nama file asli (tanpa extension)
            split_csv_file(output_csv, self.upload_dir, self.chunk_size, base_name=source_file.stem, logger=self._log)

        clear_folder_of_csvs(self.download_dir.resolve())
        return True

    def _validate_uploaded_vs_downloaded(self, upload_file, download_file) -> bool:
        upload_file = Path(upload_file)
        download_file = Path(download_file)
        """Validasi jumlah baris file upload vs download."""
        def count_lines(file_path: Path) -> int:
            for enc in ("utf-8", "latin-1", "cp1252"):
                try:
                    with open(file_path, "r", encoding=enc, errors="replace") as f:
                        return sum(1 for _ in f)
                except Exception:
                    continue
            raise ValueError(f"Gagal membaca file {file_path}")

        try:
            upload_rows = count_lines(upload_file)
            download_rows = count_lines(download_file) - 1
            if download_rows == upload_rows:
                self._log(f"Validasi OK: {download_file.name} cocok dengan {upload_file.name}")
                return True
            else:
                self._log(f"Validasi GAGAL: {download_file.name} ({download_rows}) != {upload_file.name} ({upload_rows})")
                return False
        except Exception as e:
            self._log(f"Error validasi {upload_file} vs {download_file}: {e}")
            return False

    def _process_uploads(self):
        """Upload semua CSV ke server APEX dan handle retry."""
        retry_loop = True
        upload_retry_counts = {}

        while retry_loop:
            to_upload = [
                f for f in self.upload_dir.glob("*.csv")
                if not is_already_done(self.tracker_path, f.name, "downloaded")
            ]
            failed_uploads = []

            if self.is_cancelled():
                self._log("⛔ Proses dihentikan.")
                return {"success": False, "message": "Dibatalkan oleh user."}

            for file in sorted(to_upload):
                try:
                    success = upload_all_csv_in_folder(self.driver, file.resolve(), logger=self._log)
                    update_tracker(self.tracker_path, file.name, uploaded=success)
                    if not success:
                        failed_uploads.append(file)
                except Exception as e:
                    self._log(f"Upload gagal untuk {file.name}: {e}")
                    failed_uploads.append(file)

            if not failed_uploads:
                retry_loop = False
                break

            renamed_files = []
            for file in failed_uploads:
                base_name = get_base_filename(file)
                upload_retry_counts[base_name] = upload_retry_counts.get(base_name, 0) + 1
                if upload_retry_counts[base_name] > self.upload_retry_limit:
                    self._log(f"Melebihi batas retry untuk {base_name}, skip.")
                    continue

                suffix = chr(96 + upload_retry_counts[base_name])  # a, b, c, ...
                new_file = rename_csv_file_with_suffix(file, suffix)
                renamed_files.append(new_file)

            if not renamed_files:
                retry_loop = False
                break

            for file in renamed_files:
                try:
                    success = upload_all_csv_in_folder(self.driver, file.resolve(), logger=self._log)
                    update_tracker(self.tracker_path, base_name, uploaded=success)
                except Exception as ue:
                    self._log(f"⚠️ Gagal upload ulang {base_name}: {ue}")

    def _process_downloads(self):
        """Download hasil dari server APEX dan validasi."""
        retry_loop = True
        retry_counts = {}

        while retry_loop:
            failed_downloads = []

            # ⏱️ global deadline: 2 jam dari sekarang
            GLOBAL_TIMEOUT = 60 * 120
            deadline_ts = time.time() + GLOBAL_TIMEOUT

            processed_bases = set()

            for base_filename, row in load_tracker(self.tracker_path).items():
                if row["downloaded"] == "True":
                    continue

                if self.is_cancelled():
                    self._log("⛔ Proses dihentikan.")
                    return {"success": False, "message": "Dibatalkan oleh user."}
                
                base_name = get_base_filename(Path(base_filename))

                # Hindari proses ganda untuk base_name yang sama
                if base_name in processed_bases:
                    continue
                
                # Cek lagi apakah sudah di-download di iterasi sebelumnya
                if is_already_done(self.tracker_path, base_name, "downloaded"):
                    continue

                try:
                    self.driver.refresh()
                    time.sleep(2)
                    try:
                        self.driver.find_element(By.ID, "B49871477158701971").click() # Klik tombol history
                        time.sleep(2)
                    except:
                        pass

                    upload_file = find_latest_uploaded_file(self.upload_dir, base_name)
                    if not upload_file:
                        failed_downloads.append(base_name)
                        continue

                    filename = click_download_link(
                        self.driver,
                        file_prefix=upload_file.stem,
                        deadline_ts=deadline_ts,
                        logger=self._log
                    )
                    downloaded_path = wait_for_download(self.download_dir.resolve(), base_file_name=base_name, target_filename=filename, logger=self._log)

                    if downloaded_path:
                        upload_file = find_latest_uploaded_file(self.upload_dir, base_name)

                        if not upload_file:
                            self._log(f"File upload tidak ditemukan untuk {upload_file}")
                            failed_downloads.append(base_name)
                            continue

                        is_valid = self._validate_uploaded_vs_downloaded(upload_file, downloaded_path)

                        if is_valid:
                            update_tracker(self.tracker_path, base_name, downloaded=True)
                            processed_bases.add(base_name)
                            continue
                        else:
                            failed_downloads.append(base_name)
                    else:
                        failed_downloads.append(base_name)

                except Exception as e:
                    self._log(f"Download gagal untuk {upload_file}: {e}")
                    failed_downloads.append(base_name)

            if not failed_downloads:
                retry_loop = False
                break

            # Retry untuk file gagal
            renamed_files = []

            for base_name in failed_downloads:
                stem = Path(base_name).stem

                candidates = sorted(
                    self.upload_dir.glob(f"{stem}*.csv"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )

                if not candidates:
                    self._log(f"⚠️ Tidak ada file fisik untuk retry: {base_name}")
                    continue

                orig_file = candidates[0]

                retry_counts[stem] = retry_counts.get(stem, 0) + 1
                if retry_counts[stem] > self.upload_retry_limit:
                    self._log(f"Melebihi batas retry untuk {stem}, skip.")
                    continue

                suffix = chr(96 + retry_counts[stem])
                new_file = rename_csv_file_with_suffix(orig_file, suffix)

                renamed_files.append(new_file)

            if not renamed_files:
                retry_loop = False
                break

            for file in renamed_files:
                try:
                    klik_report_status_awb(self.driver)
                    success = upload_all_csv_in_folder(self.driver, file.resolve(), logger=self._log)
                    update_tracker(self.tracker_path, base_name, uploaded=success)
                except Exception as ue:
                    self._log(f"Gagal upload ulang {base_name}: {ue}")

    def run(self):
        """Jalankan seluruh proses dari awal sampai merge hasil."""
        try:
            self._setup_folders()
            if not self.extract_awb_data():
                return {"success": False, "message": "Tidak ada AWB ditemukan."}

            self._log("Memulai proses upload/download ke APEX.")
            self.driver = start_driver(download_dir=self.download_dir.resolve())

            # 🆕 Ambil host utama yang dipilih user
            selected_host = self.get_active_host()

            # 🆕 Susun urutan host: host pilihan dulu, baru sisanya sebagai fallback
            host_candidates = [selected_host] + [h for h in self.hosts if h != selected_host]

            for host in host_candidates:
                try:
                    self._log(f"Mencoba koneksi ke host: {host}")

                    # 🆕 RESET upload state karena ganti host
                    reset_uploaded_status(self.tracker_path)

                    if self.is_cancelled():
                        self._log("⛔ Proses dihentikan.")
                        return {"success": False, "message": "Dibatalkan oleh user."}

                    _apex_url_base = os.getenv("APEX_URL_TEMPLATE", "")
                    apex_url_upload = f"{_apex_url_base.replace('{host}', host)}:::::"

                    login_to_apex(
                        self.driver,
                        url=apex_url_upload,
                        username=self.username,
                        password=self.password,
                        logger=self._log
                    )

                    self._process_uploads()
                    self._process_downloads()

                    if all_downloaded(self.tracker_path):
                        self._log("Semua file berhasil di-download.")
                        self.success = True
                        break
                    else:
                        self._log("Ada file gagal di host ini, mencoba host lain...")

                except Exception as e:
                    self._log(f"Error di host {host}: {e}")
                    self._log(traceback.format_exc())
                    continue

        finally:
            if hasattr(self, "driver") and self.driver:
                self.driver.quit()
                self._log("Browser ditutup.")

            if not getattr(self, "success", False):
                self._log("Proses selesai tapi ada file gagal di semua host.")

            # Merge hasil akhir
            merge_csv_files(
                self.download_dir.resolve(),
                self.download_dir.resolve(),
                "Updated AWB",
                date_suffix=False
            )

            if self.status_dict and self.request_id in self.status_dict:
                self.status_dict[self.request_id]["progress"] = 100
                self.status_dict[self.request_id]["done"] = True

        return {"success": getattr(self, "success", False), "message": "Selesai diproses."}
    
    def is_cancelled(self):
        return (
            self.status_dict
            and self.request_id in self.status_dict
            and self.status_dict[self.request_id].get("cancelled", False)
        )