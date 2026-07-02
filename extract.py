import os
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= PENGATURAN =================
base_folder = r"\\192.168.9.74\d\SANI\10. SCA\01. Daily\Cnote\2026\04. Apr 2026"
target_id = "80514318" 

# Folder output yang Anda minta
output_dir = r"D:\RYAN"
output_file = os.path.join(output_dir, "Hasil_Tarikan_80514318 apr.xlsx")
# ==============================================

# Fungsi untuk memproses satu file (akan dijalankan secara paralel)
def process_file(file_path, root, file):
    try:
        # Baca file Excel
        df = pd.read_excel(file_path)
        df.columns = df.columns.str.strip() # Bersihkan nama kolom
        
        if 'Puorder Cust Id' in df.columns:
            df['Puorder Cust Id'] = df['Puorder Cust Id'].astype(str).str.strip()
            filtered_df = df[df['Puorder Cust Id'] == target_id].copy()
            
            if not filtered_df.empty:
                filtered_df['Source Folder'] = os.path.basename(root)
                filtered_df['Source File'] = file
                return filtered_df
    except Exception as e:
        pass # Abaikan file yang error/corrupt agar proses tidak berhenti
    return None

def main():
    # 1. Buat folder output di Desktop jika belum ada
    os.makedirs(output_dir, exist_ok=True)
    
    file_list = []
    
    # 2. Kumpulkan semua lokasi file Excel di dalam folder Mei
    print("Mencari semua file Excel...")
    for root, dirs, files in os.walk(base_folder):
        for file in files:
            if file.endswith(('.xlsx', '.xls')):
                file_list.append((os.path.join(root, file), root, file))
                
    print(f"Menemukan {len(file_list)} file. Memulai proses ekstraksi cepat (Paralel)...")
    
    all_data = []
    
    # 3. Proses banyak file sekaligus (Multithreading) untuk mempercepat!
    # max_workers=10 berarti Python memproses 10 file sekaligus secara bersamaan
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_file, path, root, file) for path, root, file in file_list]
        
        for future in as_completed(futures):
            result = future.result()
            if result is not None and not result.empty:
                all_data.append(result)
                print(f"✅ Ditemukan {len(result)} baris dari file: {result['Source File'].iloc[0]}")

    # 4. Gabungkan dan simpan hasil
    if all_data:
        print("\nMenyatukan data dan menyimpan ke Excel...")
        final_df = pd.concat(all_data, ignore_index=True)
        final_df.to_excel(output_file, index=False)
        print(f"🎉 SELESAI! Total {len(final_df)} baris data berhasil digabungkan.")
        print(f"📂 File berhasil disimpan di: {output_file}")
    else:
        print("\nSelesai. Tidak ada data yang cocok dengan ID tersebut.")

if __name__ == "__main__":
    main()