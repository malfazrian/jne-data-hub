import csv
import os

# tracker.py

class TaskTableTracker:
    def __init__(self, task_list):
        # task_list: list of dict, minimal ada 'desc' atau 'customer_id'
        self.rows = []
        for task in task_list:
            row = {
                "task": task.get("desc") or task.get("nama_customer"),
                "request": False,
                "download": False,
                "result_path": None,
                "reason": None
            }
            self.rows.append(row)

    def set_reason(self, task, reason):
        """Set alasan gagal untuk task tertentu."""
        for row in self.rows:
            if str(row["task"]) == str(task):
                # Simpan hanya 200 karakter biar aman di JSON/HTML
                row["reason"] = (reason or "").strip()[:200]
                break

    def set_request(self, task_name, value=True):
        for row in self.rows:
            if str(row["task"]) == str(task_name):
                row["request"] = value

    def set_download(self, task_name, value=True):
        for row in self.rows:
            if str(row["task"]) == str(task_name):
                row["download"] = value

    def set_path(self, task_name, value=None):
        for row in self.rows:
            if str(row["task"]) == str(task_name):
                row["result_path"] = value

    def summary(self, output_folder="data/tracker", filename="tracker_summary.csv"):
        print("\n=== TASK TABLE TRACKER ===")
        print(f"{'Task':<40} {'Request':<10} {'Download':<10} {'Result Path'}")
        for row in self.rows:
            print(f"{row['task']:<40} {str(row['request']):<10} {str(row['download']):<10} {str(row['result_path'])}")
        
        os.makedirs(output_folder, exist_ok=True)
        output_path = os.path.join(output_folder, filename)
        with open(output_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Task", "Request", "Download", "Result Path", "Reason"])
            for row in self.rows:
                writer.writerow([
                    row["task"],
                    row["request"],
                    row["download"],
                    row.get("result_path", ""),
                    row.get("reason", "")
                ])
        print(f"Tracker summary disimpan ke: {output_path}")

# Contoh inisialisasi:
# from tasks.ryan_tasks import open_awb_tasks
# tracker = TaskTableTracker(open_awb_tasks)