import os
from .query_performance_custom import process_projects

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Contoh default config, bisa diganti dari Flask
BASE_PATH = os.getenv("CSV_BASE_PATH", "")
ARCHIVE_DIR = os.path.join(os.getcwd(), OUTPUT_DIR)

def parse_yy_mm(inp):
    tahun = int("20" + inp[:2])
    bulan = int(inp[2:])
    return tahun, bulan

def run_processing(start_yy_mm, end_yy_mm, project_lists):
    """
    Jalankan proses utama dan kembalikan list file hasil.
    """
    saved_files = process_projects(
        project_lists=project_lists,
        start_yy_mm=start_yy_mm,
        end_yy_mm=end_yy_mm,
        base_path=BASE_PATH,
        archive_dir=ARCHIVE_DIR
    )
    # Misal kembalikan file pertama sebagai default untuk download
    return saved_files[0] if saved_files else None
