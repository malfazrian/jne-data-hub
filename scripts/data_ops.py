import os
import pandas as pd

def extract_awb_from_excel(file_path):
    awb_values = set()
    xls = pd.ExcelFile(file_path)
    for sheet in xls.sheet_names:
        try:
            df_sample = pd.read_excel(file_path, sheet_name=sheet, nrows=1)
            lower_columns = [col.lower().strip() for col in df_sample.columns]

            if "awb" in lower_columns:
                original_awb_col = df_sample.columns[lower_columns.index("awb")]
                df = pd.read_excel(file_path, sheet_name=sheet, usecols=[original_awb_col], dtype=str)
                df[original_awb_col] = df[original_awb_col].astype(str).str.strip()
                awb_values.update(df[original_awb_col].dropna().tolist())
        except Exception as e:
            print(f"Gagal membaca sheet '{sheet}' di file '{file_path}': {e}")
    return awb_values

def extract_awb_from_csv(file_path):
    """
    Ekstrak kolom AWB dari file CSV dengan fallback encoding.
    """
    encodings_to_try = ["utf-8", "cp1252", "latin1", "ISO-8859-1"]

    for enc in encodings_to_try:
        try:
            # baca 1 baris dulu buat cek kolom
            df_sample = pd.read_csv(file_path, nrows=1, encoding=enc)
            lower_columns = [col.lower().strip() for col in df_sample.columns]

            if "awb" in lower_columns:
                original_awb_col = df_sample.columns[lower_columns.index("awb")]
                df = pd.read_csv(file_path, usecols=[original_awb_col], dtype=str, encoding=enc)
                df[original_awb_col] = df[original_awb_col].astype(str).str.strip()
                return set(df[original_awb_col].dropna().tolist())

            print(f"[!] Kolom 'awb' tidak ditemukan di {file_path}")
            return set()

        except UnicodeDecodeError:
            # coba encoding berikutnya
            continue
        except Exception as e:
            print(f"Gagal membaca file CSV '{file_path}' dengan encoding {enc}: {e}")
            return set()

    print(f"[!] Tidak bisa decode file CSV {file_path} dengan encoding umum (utf-8/cp1252/latin1/ISO-8859-1)")
    return set()

def extract_all_awbs_from_folder(folder_path):
    all_files = [f for f in os.listdir(folder_path) if f.endswith((".xlsx", ".csv"))]
    awb_set = set()

    for file in all_files:
        file_path = os.path.join(folder_path, file)
        try:
            if file.endswith(".xlsx"):
                awb_set.update(extract_awb_from_excel(file_path))
            elif file.endswith(".csv"):
                awb_set.update(extract_awb_from_csv(file_path))
        except Exception as e:
            print(f"Gagal memproses file '{file}': {e}")
    
    return awb_set

def save_awbs_to_csv(awb_set, output_path):
    awb_list = [f"'{awb}" for awb in sorted(awb_set)]
    df_awb = pd.DataFrame(awb_list, columns=["AWB"])
    df_awb.to_csv(output_path, index=False, header=False, encoding="utf-8")
    print(f"Proses selesai! Data AWB disimpan di: {output_path}")