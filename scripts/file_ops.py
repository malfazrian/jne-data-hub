import os
import glob
import time
import csv
import re
import shutil
import pandas as pd
import random
import win32com.client
from pathlib import Path
from datetime import datetime
from pywintypes import com_error
from typing import Union, List
from .data_ops import normalize_awb_value

def ensure_folder(path):
    if not os.path.exists(path):
        os.makedirs(path)

def clear_folder_of_csvs(folder):
    for file in os.listdir(folder):
        if file.endswith(".csv"):
            os.remove(os.path.join(folder, file))

def move_csvs_to_folder(source_dir, destination_dir):
    print(f"Mencari file CSV di: {source_dir}")
    for file in os.listdir(source_dir):
        if file.endswith(".csv"):
            src = os.path.join(source_dir, file)
            dst = os.path.join(destination_dir, file)
            print(f"Memindahkan {file} ke {destination_dir}")
            shutil.move(src, dst)

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

def backup_open_awb_files():
    """Backup file open AWB ke folder Archive dan bersihkan arsip sebelumnya."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.abspath(os.path.join(current_dir, "..", "data"))
    archive_dir = os.path.join(data_dir, "archive")

    ensure_folder(archive_dir)
    clear_folder_of_csvs(archive_dir)
    move_csvs_to_folder(data_dir, archive_dir)

    print("Backup selesai.")

def get_csv_files(folder: Path) -> list[Path]:
    """Mengambil semua file CSV dari folder tertentu."""
    return [f for f in folder.glob("*.csv") if f.is_file()]

def generate_output_filename(base_name: str, index: int, random_number: int, tanggal: str, output_folder: Path) -> Path:
    """Membuat nama file output berdasarkan format yang diinginkan."""
    return output_folder / f"{base_name} {tanggal} {index}_{random_number}.csv"

def split_csv_file(file_path: Path, output_folder: Path, chunk_size: int = 9999, base_name: str = "update awb", logger=print):
    """Membagi file CSV menjadi beberapa bagian dan menyimpannya ke folder output."""
    try:
        df = pd.read_csv(file_path, usecols=[0], header=None, dtype=str, names=["awb"])
        df["awb"] = df["awb"].apply(normalize_awb_value)
        df = df[df["awb"].notna()]
        df["awb"] = df["awb"].apply(lambda x: f"'{x}")  # Tambahkan petik satu agar Excel baca sebagai teks

        tanggal = datetime.today().strftime("%d%m%y")

        for i, start in enumerate(range(0, len(df), chunk_size), start=1):
            chunk = df.iloc[start:start + chunk_size]
            random_number = random.randint(10000, 99999)
            output_file = generate_output_filename(base_name, i, random_number, tanggal, output_folder)
            chunk.to_csv(output_file, index=False, header=False, encoding="utf-8")
            logger(f"File '{output_file.name}' berhasil dibuat.")

    except Exception as e:
        logger(f"Terjadi kesalahan saat memproses '{file_path.name}': {e}")

def get_base_filename(filename: str) -> str:
    """
    Menghapus suffix retry seperti 'a', 'b', dst setelah underscore terakhir.
    """
    base, ext = os.path.splitext(filename)
    # Hapus 1 huruf di akhir jika setelah underscore dan huruf itu adalah retry suffix
    if re.match(r".+_\d+[a-zA-Z]$", base):
        base = base[:-1]
    return base + ext

def update_tracker(tracker_path, filename, uploaded=None, downloaded=None):
    tracker = {}
    if tracker_path.exists():
        with open(tracker_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                tracker[row['filename']] = row

    base_filename = get_base_filename(filename)

    if base_filename not in tracker:
        tracker[base_filename] = {'filename': base_filename, 'uploaded': 'False', 'downloaded': 'False'}

    if uploaded is not None:
        tracker[base_filename]['uploaded'] = str(uploaded)

    if downloaded is not None:
        tracker[base_filename]['downloaded'] = str(downloaded)

    with open(tracker_path, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['filename', 'uploaded', 'downloaded']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in tracker.values():
            writer.writerow(row)

def load_tracker(tracker_path):
    """
    Load tracker CSV sebagai dict:
    {
        base_filename: {
            'filename': base_filename,
            'uploaded': bool,
            'downloaded': bool
        }
    }
    """
    tracker = {}

    if not tracker_path.exists():
        return tracker

    with open(tracker_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            filename = row.get("filename")
            if not filename:
                continue

            tracker[filename] = {
                "filename": filename,
                "uploaded": row.get("uploaded") == "True",
                "downloaded": row.get("downloaded") == "True"
            }

    return tracker

def is_already_done(tracker_path, filename, check_type="downloaded"):
    if not tracker_path.exists():
        return False
    with open(tracker_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['filename'] == filename and row.get(check_type) == 'True':
                return True
    return False

def all_downloaded(tracker_path):
    if not tracker_path.exists():
        return False
    with open(tracker_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row.get("uploaded") == "True" and row.get("downloaded") != "True":
                return False
    return True

def reset_uploaded_status(tracker_path):
    if not tracker_path.exists():
        return

    with open(tracker_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        filenames = [row['filename'] for row in reader]

    for fname in filenames:
        update_tracker(tracker_path, fname, uploaded=False)

def rename_csv_file_with_suffix(file_path: Path, suffix: str) -> Path:
    """Rename a CSV file by appending a suffix before the extension."""
    new_name = f"{file_path.stem}{suffix}{file_path.suffix}"
    new_path = file_path.parent / new_name
    counter = ord(suffix)
    while new_path.exists():
        counter += 1
        new_name = f"{file_path.stem}{chr(counter)}{file_path.suffix}"
        new_path = file_path.parent / new_name
    file_path.rename(new_path)
    return new_path

def wait_for_query_refresh(wb, timeout=3600):
    """Tunggu semua koneksi query hingga selesai refresh (maks 1 jam)"""
    start_time = time.time()
    while True:
        refreshing = False
        try:
            for connection in wb.Connections:
                try:
                    if connection.OLEDBConnection.Refreshing:
                        refreshing = True
                        break
                except AttributeError:
                    continue
        except com_error as e:
            if e.hresult == -2147418111:  # Call was rejected by callee
                print("Excel sedang sibuk... tunggu sebentar.")
                time.sleep(2)
                continue
            else:
                raise

        if not refreshing:
            break

        if time.time() - start_time > timeout:
            raise TimeoutError("Timeout: Query terlalu lama tidak selesai.")

        print("Menunggu query selesai...")
        time.sleep(2)


def refresh_excel_workbooks(target: Union[str, Path, List[Union[str, Path]]]):
    """
    Refresh semua koneksi dalam satu atau banyak file Excel (.xlsx).

    Args:
        target (str | Path | List[str | Path]):
            - Path ke folder (semua .xlsx di-refresh)
            - Atau list path file Excel

    Returns:
        None
    """
    if isinstance(target, (str, Path)):
        target = Path(target)
        if target.is_dir():
            file_list = sorted(target.glob("*.xlsx"))
        elif target.is_file():
            file_list = [target]
        else:
            raise FileNotFoundError(f"Path tidak ditemukan: {target}")
    elif isinstance(target, list):
        file_list = [Path(f) for f in target if Path(f).suffix == ".xlsx"]
    else:
        raise ValueError("Parameter 'target' harus folder, file, atau list file.")

    if not file_list:
        print("Tidak ada file Excel ditemukan untuk diproses.")
        return

    print("Membuka Excel...")
    excel = win32com.client.Dispatch("Excel.Application")
    excel.Visible = False

    try:
        for excel_file in file_list:
            print(f"\nMembuka file: {excel_file.name}")
            wb = excel.Workbooks.Open(str(excel_file))

            print("Merefresh semua koneksi data...")
            wb.RefreshAll()

            wait_for_query_refresh(wb)

            wb.Save()
            wb.Close(SaveChanges=True)
            print("Refresh selesai dan file disimpan.")

    except Exception as e:
        print(f"Terjadi kesalahan: {e}")

    finally:
        excel.Quit()
        print("\nSemua file telah diproses.")
