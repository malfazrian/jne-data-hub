from datetime import datetime, timedelta
import time
import os
import glob
from pathlib import Path
import pandas as pd
from scripts.tracker import TaskTableTracker
from urllib.parse import unquote
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException

def start_driver(download_dir: str):
    options = webdriver.ChromeOptions()
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    options.add_argument("--log-level=3")
    options.add_argument("--headless")
    return webdriver.Chrome(options=options)

def login_to_apex(driver, url, username, password):
    driver.get(url)
    time.sleep(2)
    print("Login ke sistem...")
    driver.find_element(By.NAME, "p_t01").clear()
    driver.find_element(By.NAME, "p_t01").send_keys(username)
    driver.find_element(By.NAME, "p_t02").send_keys(password + Keys.RETURN)
    time.sleep(2)

def generate_date_ranges(
    tgl_awal_str: str,
    tgl_akhir_str: str,
    format: str = "%d-%b-%Y",
    max_range: int = 5
):
    tgl_awal = datetime.strptime(tgl_awal_str, format)
    tgl_akhir = datetime.strptime(tgl_akhir_str, format)

    ranges = []

    current = tgl_awal
    while current <= tgl_akhir:
        # batas normal (max_range)
        candidate_end = current + timedelta(days=max_range - 1)

        # batas akhir tahun
        end_of_year = datetime(current.year, 12, 31)

        # ambil yang paling kecil
        tgl_end = min(candidate_end, end_of_year, tgl_akhir)

        ranges.append((
            current.strftime(format),
            tgl_end.strftime(format)
        ))

        current = tgl_end + timedelta(days=1)

    return ranges

def isi_tanggal_awal_akhir(driver, tanggal_awal: str, tanggal_akhir: str):
    for field_id, value in [("P45_DATE1", tanggal_awal), ("P45_DATE2", tanggal_akhir)]:
        elem = driver.find_element(By.ID, field_id)
        elem.clear()
        elem.send_keys(value)

def fill_form_request(driver, customer_id: str, tanggal_awal: str, tanggal_akhir: str, retries: int = 5):
    for attempt in range(retries):
        try:
            try:
                input_cust = driver.find_element(By.ID, "P45_CUST")
                if customer_id.startswith("8"):
                    driver.find_element(By.ID, "P45_NA").send_keys(Keys.ARROW_UP)
                    time.sleep(2)
            except NoSuchElementException:
                driver.find_element(By.ID, "P45_PARAM_TYPE").send_keys(Keys.ARROW_DOWN)
                time.sleep(2)
                if customer_id.startswith("8"):
                    driver.find_element(By.ID, "P45_NA").send_keys(Keys.ARROW_UP)
                    time.sleep(2)
                driver.find_element(By.ID, "P45_PARAM_TYPE").send_keys(Keys.ARROW_DOWN)
                driver.refresh()
                time.sleep(3)
                input_cust = driver.find_element(By.ID, "P45_CUST")

            input_cust.clear()
            input_cust.send_keys(customer_id)
            time.sleep(1)
            input_cust.send_keys(Keys.ARROW_DOWN)
            input_cust.send_keys(Keys.ENTER)

            isi_tanggal_awal_akhir(driver, tanggal_awal, tanggal_akhir)
            driver.find_element(By.ID, "B49858870221701936").click()

            # Tunggu muncul status message
            WebDriverWait(driver, 60*5).until(
                EC.presence_of_element_located((By.ID, "messages"))
            )

            # Cek apakah error
            messages_html = driver.find_element(By.ID, "messages").get_attribute("innerHTML")
            if "notification-message" in messages_html:
                error_text = driver.find_element(By.ID, "notification-message").text
                if "Customer id" in error_text and "not found" in error_text:
                    print(f"Customer ID tidak ditemukan: {customer_id}")
                    return False
                else:
                    raise Exception(f"Request gagal sementara: {error_text}")
            elif "success-message" in messages_html:
                print(f"Request berhasil untuk {customer_id} ({tanggal_awal} - {tanggal_akhir})")
                return True
            else:
                print("Tidak ada pesan status yang jelas, retrying...")
                time.sleep(2)
        except StaleElementReferenceException:
            time.sleep(2)
            driver.refresh()
        except RuntimeError as e:
            print(str(e))
            return False
        except Exception as e:
            time.sleep(2)
            driver.refresh()
    print(f"Gagal request untuk {customer_id} setelah {retries} kali.")
    return False

def click_download_link(driver, customer_id: str = None, start_fmt: str = None, end_fmt: str = None):
    print("Menunggu link download siap (auto-refresh setiap 10 detik)...")

    if customer_id and start_fmt and end_fmt:
        search_key = f"_{customer_id}_{start_fmt}_{end_fmt}_"
    else:
        raise ValueError("Harus isi file_prefix ATAU customer_id + tanggal")

    wait = WebDriverWait(driver, timeout=10*60)  # tunggu maksimal 30 detik

    while True:
        try:
            # Tunggu sampai kolom pencarian tersedia
            search_field = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[id='Status History SLA_search_field']"))
            )
            search_field.clear()
            search_field.send_keys(search_key)
            time.sleep(2)
            driver.find_element(By.ID, "Status History SLA_search_button").click()
            time.sleep(2)

            rows = driver.find_elements(By.CSS_SELECTOR, "table.a-IRR-table tbody tr")
            for row in rows:
                try:
                    filename_text = row.find_element(By.XPATH, './td[@headers="FILENAME"]').text.strip()
                    error_text = row.find_element(By.XPATH, './td[@headers="ERROR"]').text.strip()

                    matched = f"_{customer_id}_{start_fmt}_{end_fmt}_" in filename_text

                    if not matched:
                        continue

                    if error_text and error_text != "-":
                        if any(x in error_text for x in ["500", "502", "try again"]):
                            print(f"Proses dihentikan karena error kritikal: {error_text}")
                            return None
                        if "No Data Found" in error_text:
                            print(f"Tidak ada data untuk {customer_id}, lanjut akun berikutnya.")
                            return None

                    link = row.find_element(By.XPATH, './td[@headers="DOWNLOAD"]').find_element(By.TAG_NAME, "a")
                    href = link.get_attribute("href")
                    actual_filename = unquote(href.split("p_fn=")[-1]) if "p_fn=" in href else filename_text
                    link.click()
                    return actual_filename

                except (NoSuchElementException, StaleElementReferenceException):
                    continue

            time.sleep(10)
            driver.refresh()

        except Exception as e:
            print(f"Halaman gagal dimuat: {e}")
            time.sleep(5)
            driver.refresh()

def wait_for_download(download_dir: str, timeout: int = 90, target_filename=None, contains_text=None) -> str:
    polling_interval = 2
    elapsed = 0

    while elapsed < timeout:
        for fname in os.listdir(download_dir):
            # Jika ada target_filename, tetap cek persis
            if target_filename and fname == target_filename:
                full_path = os.path.join(download_dir, fname)
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0 and not os.path.exists(full_path + ".crdownload"):
                    print(f"File berhasil diunduh: {full_path} ({os.path.getsize(full_path)} bytes)")
                    return full_path
            # Jika contains_text (misal customer_id), cek apakah ada di nama file
            elif contains_text and contains_text in fname and fname.endswith(".csv") and not fname.endswith(".crdownload"):
                full_path = os.path.join(download_dir, fname)
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0 and not os.path.exists(full_path + ".crdownload"):
                    print(f"File berhasil diunduh (by contains): {full_path} ({os.path.getsize(full_path)} bytes)")
                    return full_path
            # Fallback: cek nama file yang diawali base_file_name
            elif not target_filename and not contains_text:
                full_path = os.path.join(download_dir, fname)
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0 and not os.path.exists(full_path + ".crdownload"):
                    print(f"File berhasil diunduh: {full_path} ({os.path.getsize(full_path)} bytes)")
                    return full_path

        time.sleep(polling_interval)
        elapsed += polling_interval

    print("File tidak ditemukan atau belum selesai dalam batas waktu.")
    return ""

def merge_csv_files(
    input_folder: Path,
    output_folder: Path,
    filename_prefix: str = "data",
    date_suffix: bool | str = True,  # True=pakai tanggal hari ini, str=custom, False=tanpa tanggal
) -> str:
    """
    Menggabungkan semua file CSV dalam folder input menjadi satu file.

    Args:
        input_folder (Path): Folder sumber CSV.
        output_folder (Path): Folder hasil gabungan.
        filename_prefix (str): Prefix nama file output.
        date_suffix (bool | str): Jika True → pakai tanggal hari ini,
                                  Jika str → pakai string custom,
                                  Jika False → tanpa suffix tanggal.

    Returns:
        str: Path lengkap file hasil gabungan.
    """
    # Tentukan nama file output
    if date_suffix is True:
        suffix = datetime.today().strftime("%d%m%y")
        filename = f"{filename_prefix}_{suffix}.csv"
    elif isinstance(date_suffix, str):
        filename = f"{filename_prefix}_{date_suffix}.csv"
    else:
        filename = f"{filename_prefix}.csv"

    output_path = output_folder / filename

    # Cari file CSV
    csv_files = glob.glob(str(input_folder / "*.csv"))

    if not csv_files:
        print("❌ Tidak ada file CSV ditemukan.")
        return ""

    data_frames = []
    encodings_to_try = [
        "utf-8", "utf-8-sig", "latin1", "windows-1252", "iso-8859-1"
    ]

    for file in csv_files:
        for enc in encodings_to_try:
            try:
                df = pd.read_csv(file, encoding=enc)
                print(f"✅ Dibaca: {os.path.basename(file)} (encoding: {enc})")
                data_frames.append(df)
                break
            except Exception:
                continue
        else:
            print(f"⚠️ Gagal baca file: {file}, dilewati.")

    if not data_frames:
        print("❌ Tidak ada file berhasil digabung.")
        return ""

    # Gabung semua dataframe
    merged_df = pd.concat(data_frames, ignore_index=True)

    # Simpan
    output_folder.mkdir(parents=True, exist_ok=True)
    merged_df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"\n✅ Semua file berhasil digabung ke: {output_path}")
    return str(output_path.resolve())

class ApexRequestProcessor:
    """
    Class untuk memproses request & download data APEX per user.
    Setiap instance bisa dipakai untuk 1 user tanpa bentrok antar user lain.
    """

    def __init__(
        self,
        download_dir,
        list_customer_ids,
        tanggal_awal,
        tanggal_akhir=None,
        apex_hosts=None,
        username=None,
        password=None,
        task_id=None,
        task_store=None,
    ):
        self.task_id = task_id or str(int(time.time()))
        self.task_store = task_store
        self.download_dir = download_dir
        self.list_customer_ids = list_customer_ids
        self.tanggal_awal = tanggal_awal
        self.tanggal_akhir = tanggal_akhir or (datetime.today() - timedelta(days=1)).strftime("%d-%b-%Y")
        self.apex_hosts = apex_hosts or [h.strip() for h in os.getenv("APEX_UPLOAD_HOSTS", "").split(",") if h.strip()]
        self.username = username
        self.password = password

        # Inisialisasi tracker per instance
        expanded_task_list = self._generate_task_list()
        self.tracker = TaskTableTracker(expanded_task_list)
        self.progress_callback = None

    def _generate_task_list(self):
        """Buat daftar semua task berdasarkan customer dan tanggal"""
        expanded = []
        for cust in self.list_customer_ids:
            customer_id = cust["customer_id"]
            date_ranges = generate_date_ranges(self.tanggal_awal, self.tanggal_akhir)
            for start, end in date_ranges:
                expanded.append({"desc": f"{customer_id}_{start}_to_{end}"})
        return expanded

    def _connect_and_request(self, host):
        if self.is_cancelled():
            print("⛔ Task dibatalkan sebelum koneksi host.")
            return False

        """Proses utama ke satu host APEX"""
        print(f"=== Menghubungkan ke host: {host} ===")
        _apex_url_base = os.getenv("APEX_URL_TEMPLATE", "")
        apex_url = f"{_apex_url_base.replace('{host}', host)}::NO::P45_PARAM_TYPE:P"
        driver = start_driver(self.download_dir)
        request_info = []

        total_tasks = len(self.tracker.rows)
        completed = 0

        try:            
            login_to_apex(driver, apex_url, self.username, self.password)

            for cust in self.list_customer_ids:
                customer_id = cust["customer_id"]
                date_ranges = generate_date_ranges(self.tanggal_awal, self.tanggal_akhir)

                for start, end in date_ranges:
                    if self.is_cancelled():
                        print("⛔ Task dibatalkan saat request.")
                        return False
                    
                    task_name = f"{customer_id}_{start}_to_{end}"
                    row = next((r for r in self.tracker.rows if str(r["task"]) == task_name), None)
                    if row and row["download"]:
                        print(f"✅ Skip (sudah download): {task_name}")
                        continue

                    try:
                        success = fill_form_request(driver, customer_id, start, end)
                        if success:
                            self.tracker.set_request(task_name, True)
                            request_info.append(
                                (
                                    customer_id,
                                    datetime.strptime(start, "%d-%b-%Y").strftime("%y%m%d"),
                                    datetime.strptime(end, "%d-%b-%Y").strftime("%y%m%d"),
                                    task_name,
                                )
                            )
                    except Exception as e:
                        print(f"❌ Request gagal untuk {customer_id} ({start}-{end}): {e}")
                        if row:
                            self.tracker.set_reason(task_name, f"Request error: {e[:200]}")

                    if self.progress_callback:
                        total = len(self.tracker.rows)
                        req_done = sum(1 for r in self.tracker.rows if r["request"])
                        dl_done = sum(1 for r in self.tracker.rows if r["download"])
                        progress = int(((req_done / total) * 50) + ((dl_done / total) * 50))
                        self.progress_callback(min(progress, 100), self.tracker.rows)


            # Klik tombol History (optional)
            try:
                history_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "B49871477158701971"))
                )
                history_button.click()
            except Exception:
                pass

            # Download hasil request
            for customer_id, start_fmt, end_fmt, task_name in request_info:
                if self.is_cancelled():
                    print("⛔ Task dibatalkan saat download.")
                    return False
                
                row = next((r for r in self.tracker.rows if str(r["task"]) == task_name), None)
                if row and row["download"]:
                    continue

                try:
                    filename = click_download_link(driver, customer_id, start_fmt, end_fmt)

                    if filename:
                        path = wait_for_download(self.download_dir, target_filename=filename, contains_text=customer_id)
                        if path:
                            self.tracker.set_download(task_name, True)
                            self.tracker.set_path(task_name, path)
                    else:
                        # Tambahkan handler kalau filename None
                        if row and not row["download"]:
                            self.tracker.set_reason(task_name, f"Tidak ada data untuk {customer_id} ({start_fmt}-{end_fmt})")

                    time.sleep(10)
                    driver.refresh()

                except Exception as e:
                    error_msg = str(e)
                    print(f"❌ Download gagal untuk {customer_id}: {error_msg}")
                    if row:
                        self.tracker.set_reason(task_name, error_msg[:200])

                if self.progress_callback:
                    if self.is_cancelled():
                        return False
                    
                    total = len(self.tracker.rows)
                    req_done = sum(1 for r in self.tracker.rows if r["request"])
                    dl_done = sum(1 for r in self.tracker.rows if r["download"])
                    progress = int(((req_done / total) * 50) + ((dl_done / total) * 50))
                    self.progress_callback(min(progress, 100), self.tracker.rows)


            return True

        except Exception as e:
            print(f"⚠️ Error di host {host}: {e}")
            return False

        finally:
            driver.quit()
            print("Browser ditutup.")
            self.tracker.summary(output_folder=os.path.join(self.download_dir, "tracker"))

    def run(self, merge_after=True):
        """Jalankan ke semua host sampai berhasil"""
        for host in self.apex_hosts:
            if self._connect_and_request(host):
                if merge_after:
                    merged_path = self.merge_results()
                    print(f"✅ File gabungan disimpan di: {merged_path}")
                return True
        return False

    def merge_results(self):
        """Gabungkan semua file CSV hasil download ke satu file"""
        output_folder = Path(self.download_dir) / "merged"
        output_folder.mkdir(parents=True, exist_ok=True)

        # Ambil semua ID unik
        customer_ids = [str(c["customer_id"]) for c in self.list_customer_ids]
        unique_ids = sorted(set(customer_ids))

        # Gabungkan jadi string, batasi panjang (biar nama file tidak kepanjangan)
        ids_str = "-".join(unique_ids)
        if len(ids_str) > 60:
            ids_str = ids_str[:60] + "_etc"

        # Buat nama file lebih bermakna
        filename_prefix = f"merged_{ids_str}"

        merged_path = merge_csv_files(
            input_folder=Path(self.download_dir),
            output_folder=output_folder,
            filename_prefix=filename_prefix,
            date_suffix=True
        )

        return merged_path
    
    def is_cancelled(self):
        if not self.task_store:
            return False
        return self.task_store.get(self.task_id, {}).get("cancelled", False)