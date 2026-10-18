from __future__ import annotations

import hashlib
import json
import os
import queue
import string
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable


APP_NAME = "File-Yeti"
APP_SUBTITLE = "Visual storage review"
CHUNK_SIZE = 1024 * 1024
DEFAULT_LARGE_FILE_SIZE = 500 * 1024 * 1024
HISTORY_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "File-Yeti" / "scan_history.json"
PROTECTED_ROOTS = {
    Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve(),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve(),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")).resolve(),
    Path(os.environ.get("ProgramData", r"C:\ProgramData")).resolve(),
}


@dataclass(frozen=True)
class FileRecord:
    path: Path
    size: int
    modified: float
    category: str


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    files: tuple[FileRecord, ...]


def format_size(value: int | float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def format_date(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "unknown"


def count_files(root: Path, stop_event: threading.Event) -> int:
    count = 0
    for _path in iter_files(root, stop_event):
        if stop_event.is_set():
            break
        count += 1
    return count


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "Calculating…"
    seconds = int(seconds)
    minutes, remaining = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m left"
    if minutes:
        return f"{minutes}m {remaining:02d}s left"
    return f"{remaining}s left"


def safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def classify_path(path: Path, user_root: Path, system_root: Path) -> str:
    resolved = safe_resolve(path) or path
    temp_roots = {
        Path(os.environ.get("TEMP", "")).resolve(),
        Path(os.environ.get("TMP", "")).resolve(),
        user_root / "AppData" / "Local" / "Temp",
        system_root / "Temp",
    }
    if any(is_under(resolved, root) for root in temp_roots if str(root)):
        return "Temporary"
    if is_under(resolved, user_root):
        return "User"
    if any(is_under(resolved, root) for root in PROTECTED_ROOTS):
        return "System"
    return "Other"


def iter_files(root: Path, stop_event: threading.Event) -> Iterable[Path]:
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if stop_event.is_set():
                    return
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        yield from iter_files(Path(entry.path), stop_event)
                    elif entry.is_file(follow_symlinks=False):
                        yield Path(entry.path)
                except (OSError, PermissionError):
                    continue
    except (OSError, PermissionError):
        return


def hash_file(path: Path, stop_event: threading.Event) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                if stop_event.is_set():
                    return None
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, PermissionError):
        return None


class CleanerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1120x720")
        self.minsize(900, 560)
        self.configure(bg="#f4f7fb")
        icon_path = Path(__file__).with_name("file-yeti.ico")
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except tk.TclError:
                pass
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.style.configure("TFrame", background="#f4f7fb")
        self.style.configure("TLabel", background="#f4f7fb", foreground="#172033")
        self.style.configure("Header.TLabel", background="#172033", foreground="#ffffff", font=("Segoe UI", 22, "bold"))
        self.style.configure("Subtitle.TLabel", background="#172033", foreground="#b9c5d6", font=("Segoe UI", 10))
        self.style.configure("Muted.TLabel", foreground="#64748b", background="#f4f7fb")
        self.style.configure("Status.TLabel", foreground="#334155", background="#f4f7fb", font=("Segoe UI", 10, "bold"))
        self.style.configure("Treeview", rowheight=28, background="#ffffff", fieldbackground="#ffffff", foreground="#172033")
        self.style.configure("Treeview.Heading", background="#e7edf5", foreground="#334155", font=("Segoe UI", 10, "bold"))
        self.style.configure("Accent.TButton", foreground="#ffffff", background="#1769aa", padding=(14, 8), font=("Segoe UI", 10, "bold"))
        self.style.map("Accent.TButton", background=[("active", "#125487")])
        self.style.configure("Danger.TButton", foreground="#9b1c1c")

        self.drive_var = tk.StringVar(value=self.default_drive())
        self.scan_status = tk.StringVar(value="Ready · scanning is read-only")
        self.scan_detail = tk.StringVar(value="Choose a drive or folder to begin")
        self.progress_var = tk.DoubleVar(value=0)
        self.scan_total = 0
        self.scan_started = 0.0
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.records: list[FileRecord] = []
        self.duplicates: list[DuplicateGroup] = []
        self.large_records: list[FileRecord] = []
        self.selected_delete: set[Path] = set()
        self.current_root = Path(self.drive_var.get().strip() or self.default_drive())
        self.build_ui()
        self.after(150, self.process_events)
        self.update_storage_card()

    @staticmethod
    def default_drive() -> str:
        drive = os.environ.get("SystemDrive", "C:")
        return drive if drive.endswith("\\") else drive + "\\"

    def build_ui(self) -> None:
        top = tk.Frame(self, bg="#172033", padx=24, pady=20)
        top.pack(fill="x")
        ttk.Label(top, text="File-Yeti", style="Header.TLabel").pack(side="left")
        ttk.Label(top, text=f"  {APP_SUBTITLE} · review before delete", style="Subtitle.TLabel").pack(side="left")
        ttk.Button(top, text="Help", command=self.show_help).pack(side="right", padx=(8, 0))
        ttk.Button(top, text="Choose folder", command=self.choose_folder).pack(side="right", padx=(8, 0))
        self.pause_button = ttk.Button(top, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_button.pack(side="right")
        ttk.Button(top, text="Cancel", command=self.cancel_scan).pack(side="right", padx=8)
        ttk.Button(top, text="Scan now", style="Accent.TButton", command=self.start_scan).pack(side="right", padx=8)

        controls = ttk.Frame(self, padding=(22, 0, 22, 10))
        controls.pack(fill="x")
        ttk.Label(controls, text="Scan root:").pack(side="left")
        ttk.Entry(controls, textvariable=self.drive_var, width=50).pack(side="left", padx=8)
        ttk.Label(controls, text="No files are deleted during scanning.", style="Muted.TLabel").pack(side="left")

        self.storage_card = tk.Frame(self, bg="#ffffff", highlightbackground="#dbe4ef", highlightthickness=1, padx=16, pady=12)
        self.storage_card.pack(fill="x", padx=24, pady=(12, 0))
        tk.Label(self.storage_card, text="Storage overview", bg="#ffffff", fg="#172033", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.storage_value = tk.Label(self.storage_card, text="Select a drive to view capacity", bg="#ffffff", fg="#64748b", font=("Segoe UI", 10))
        self.storage_value.pack(anchor="w", pady=(4, 0))
        self.storage_bar = ttk.Progressbar(self.storage_card, maximum=100, mode="determinate")
        self.storage_bar.pack(fill="x", pady=(8, 0))
        ttk.Label(self, textvariable=self.scan_status, style="Status.TLabel").pack(anchor="w", padx=24, pady=(10, 0))
        ttk.Label(self, textvariable=self.scan_detail, style="Muted.TLabel").pack(anchor="w", padx=24, pady=(2, 0))
        self.progress = ttk.Progressbar(self, variable=self.progress_var, maximum=100, mode="determinate")
        self.progress.pack(fill="x", padx=24, pady=(8, 4))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=22, pady=16)
        self.summary_frame = ttk.Frame(self.notebook, padding=14)
        self.large_frame = ttk.Frame(self.notebook, padding=14)
        self.duplicate_frame = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.summary_frame, text="Overview")
        self.notebook.add(self.large_frame, text="Large files")
        self.notebook.add(self.duplicate_frame, text="Duplicates")
        self.build_summary()
        self.build_large_files()
        self.build_duplicates()
        self.history_frame = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.history_frame, text="History")
        self.build_history()

        bottom = ttk.Frame(self, padding=(22, 0, 22, 18))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Clear review queue", command=self.clear_queue).pack(side="left")
        ttk.Button(bottom, text="Delete selected", style="Danger.TButton", command=self.delete_selected).pack(side="right")
        ttk.Label(bottom, text="Protected system roots are never queued by default.", style="Muted.TLabel").pack(side="right", padx=12)

    def build_history(self) -> None:
        ttk.Label(self.history_frame, text="Recent scan summaries are stored locally on this computer.", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.history_tree = self.make_tree(self.history_frame, ("time", "root", "status", "files", "size"), ("Date", "Root", "Status", "Files", "Total size"), (160, 420, 150, 100, 130))
        ttk.Button(self.history_frame, text="Clear history", command=self.clear_history).pack(anchor="e", pady=(8, 0))
        self.load_history()

    def load_history(self) -> None:
        self.clear_tree(self.history_tree)
        try:
            entries = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            entries = []
        for entry in entries[-30:][::-1]:
            self.history_tree.insert("", "end", values=(entry.get("time", ""), entry.get("root", ""), entry.get("status", ""), entry.get("files", ""), entry.get("size", "")))

    def save_history(self, root: Path, records: list[FileRecord], totals: dict[str, list[int]], cancelled: bool) -> None:
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            try:
                entries = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                entries = []
            total_size = sum(value[0] for value in totals.values())
            entries.append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "root": str(root), "status": "Cancelled" if cancelled else "Complete", "files": f"{len(records):,}", "size": format_size(total_size)})
            HISTORY_FILE.write_text(json.dumps(entries[-30:], indent=2), encoding="utf-8")
        except OSError:
            pass
        self.load_history()

    def clear_history(self) -> None:
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        self.load_history()
    def build_summary(self) -> None:
        self.summary_tree = self.make_tree(self.summary_frame, ("category", "size", "files"), ("Category", "Total size", "Files"), (180, 180, 120))



    def build_large_files(self) -> None:
        ttk.Label(self.large_frame, text="Files ≥ 500 MB. Select a row to add it to the review queue.", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.large_tree = self.make_tree(self.large_frame, ("select", "tier", "size", "modified", "path"), ("Queue", "Tier", "Size", "Modified", "Path"), (80, 120, 110, 150, 600))
        self.large_tree.bind("<Double-1>", lambda _event: self.toggle_selected(self.large_tree))

    def build_duplicates(self) -> None:
        ttk.Label(self.duplicate_frame, text="Duplicate candidates are grouped by SHA-256 and size. Nothing is selected automatically.", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.duplicate_tree = self.make_tree(self.duplicate_frame, ("select", "size", "modified", "path"), ("Queue", "Size", "Modified", "Path"), (80, 110, 150, 720))
        self.duplicate_tree.bind("<Double-1>", lambda _event: self.toggle_selected(self.duplicate_tree))

    @staticmethod
    def make_tree(parent: tk.Widget, columns: tuple[str, ...], headings: tuple[str, ...], widths: tuple[int, ...]) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return tree

    def update_storage_card(self) -> None:
        try:
            import shutil
            usage = shutil.disk_usage(Path(self.drive_var.get().strip() or self.default_drive()))
            used_percent = usage.used / usage.total * 100 if usage.total else 0
            self.storage_bar["value"] = used_percent
            self.storage_value.configure(text=f"{format_size(usage.used)} used of {format_size(usage.total)} · {format_size(usage.free)} available · {used_percent:.0f}% used")
        except (OSError, ValueError):
            self.storage_bar["value"] = 0
            self.storage_value.configure(text="Select a valid drive to view capacity")
        self.after(1000, self.update_storage_card)

    def show_help(self) -> None:
        messagebox.showinfo(APP_NAME, "File-Yeti scans without deleting files.\n\nDouble-click large files or duplicate rows to queue them. Review the queue, then choose Delete selected to move approved files to the Recycle Bin.\n\nPause temporarily suspends scanning. Cancel keeps the partial results for review. Protected Windows folders are not queued automatically.")

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.drive_var.get() or self.default_drive())
        if chosen:
            self.drive_var.set(chosen)

    def start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        root = Path(self.drive_var.get().strip()).expanduser()
        if not root.exists() or not root.is_dir():
            messagebox.showerror(APP_NAME, "Choose an existing folder or drive before scanning.")
            return
        self.current_root = root
        self.clear_results()
        self.stop_event.clear()
        self.pause_event.clear()
        self.pause_button.configure(state="normal", text="Pause")
        self.scan_status.set(f"Scanning {root}…")
        self.worker = threading.Thread(target=self.scan_worker, args=(root,), daemon=True)
        self.worker.start()

    def toggle_pause(self) -> None:
        if not self.worker or not self.worker.is_alive():
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="Pause")
            self.scan_status.set(f"{self.progress_var.get():.0f}% complete · resuming")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="Resume")
            self.scan_status.set(f"{self.progress_var.get():.0f}% complete · paused")

    def cancel_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.pause_event.clear()
            self.pause_button.configure(state="disabled", text="Pause")
            self.scan_status.set("Cancelling safely…")

    def stop_scan(self) -> None:
        self.cancel_scan()

    def wait_if_paused(self) -> bool:
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.1)
        return self.stop_event.is_set()


    def scan_worker(self, root: Path) -> None:
        user_root = Path(os.environ.get("USERPROFILE", str(Path.home())))
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        records: list[FileRecord] = []
        totals: dict[str, list[int]] = {key: [0, 0] for key in ("System", "User", "Temporary", "Other")}
        self.scan_total = count_files(root, self.stop_event)
        if self.stop_event.is_set():
            self.events.put(("done", ([], {key: [0, 0] for key in ("System", "User", "Temporary", "Other")}, [], [])))
            return
        self.scan_started = time.monotonic()
        self.events.put(("status", (0, self.scan_total, "Preparing scan…")))
        for path in iter_files(root, self.stop_event):
            if self.wait_if_paused():
                break
            try:
                stat = path.stat()
                record = FileRecord(path, stat.st_size, stat.st_mtime, classify_path(path, user_root, system_root))
                records.append(record)
                totals[record.category][0] += record.size
                totals[record.category][1] += 1
                scanned = len(records)
                if scanned % 25 == 0 or scanned == self.scan_total:
                    elapsed = time.monotonic() - self.scan_started
                    percent = (scanned / self.scan_total * 100) if self.scan_total else 100
                    eta = (elapsed / scanned) * (self.scan_total - scanned) if scanned else None
                    self.events.put(("status", (percent, self.scan_total, f"Scanning · {scanned:,}/{self.scan_total:,} files · {format_eta(eta)}")))
            except (OSError, PermissionError):
                continue
        if self.stop_event.is_set():
            self.events.put(("done", (records, totals, sorted((record for record in records if record.size >= DEFAULT_LARGE_FILE_SIZE), key=lambda item: item.size, reverse=True), [])))
            return
        large = sorted((record for record in records if record.size >= DEFAULT_LARGE_FILE_SIZE), key=lambda item: item.size, reverse=True)
        groups: dict[tuple[int, str], list[FileRecord]] = {}
        for record in records:
            if record.size >= 1:
                groups.setdefault((record.size, record.path.suffix.lower()), []).append(record)
        duplicate_groups: list[DuplicateGroup] = []
        candidates = [record for values in groups.values() if len(values) > 1 for record in values]
        hashed: dict[tuple[int, str], list[FileRecord]] = {}
        for index, record in enumerate(candidates, start=1):
            if self.wait_if_paused():
                break
            digest = hash_file(record.path, self.stop_event)
            if digest:
                hashed.setdefault((record.size, digest), []).append(record)
            if index % 10 == 0:
                percent = 90 + (index / len(candidates) * 10 if candidates else 10)
                self.events.put(("status", (percent, self.scan_total, f"Checking duplicates · {index:,}/{len(candidates):,} candidates · ETA not available")))
        duplicate_groups = [
            DuplicateGroup(digest, tuple(sorted(matches, key=lambda item: item.modified)))
            for (size, digest), matches in hashed.items()
            if len(matches) > 1
        ]
        self.events.put(("status", (100, self.scan_total, "Finalizing results…")))
        self.events.put(("done", (records, totals, large, duplicate_groups)))

    def process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    percent, total, detail = payload
                    self.progress_var.set(percent)
                    self.scan_status.set(f"{percent:.0f}% complete")
                    self.scan_detail.set(detail)
                elif event == "done":
                    self.apply_results(payload)
        except queue.Empty:
            pass
        self.after(150, self.process_events)

    def apply_results(self, payload: object) -> None:
        records, totals, large, duplicate_groups = payload  # type: ignore[misc]
        self.records = records
        self.large_records = large
        self.duplicates = duplicate_groups
        self.pause_button.configure(state="disabled", text="Pause")
        if self.stop_event.is_set():
            self.scan_status.set("Scan cancelled · partial results shown")
        else:
            self.scan_status.set("100% complete · scan finished")
        self.scan_detail.set(f"{len(records):,} files · {len(large):,} large files · {len(duplicate_groups):,} duplicate groups")
        self.render_summary(totals)
        self.render_large()
        self.render_duplicates()
        self.save_history(self.current_root, records, totals, self.stop_event.is_set())
        self.worker = None

    def render_summary(self, totals: dict[str, list[int]]) -> None:
        self.clear_tree(self.summary_tree)
        for category, (size, count) in totals.items():
            self.summary_tree.insert("", "end", values=(category, format_size(size), f"{count:,}"))

    def render_large(self) -> None:
        self.clear_tree(self.large_tree)
        for record in self.large_records:
            self.large_tree.insert("", "end", iid=str(record.path), values=("□", self.tier(record.size), format_size(record.size), format_date(record.modified), str(record.path)))

    def render_duplicates(self) -> None:
        self.clear_tree(self.duplicate_tree)
        for group in self.duplicates:
            for record in group.files:
                self.duplicate_tree.insert("", "end", iid=str(record.path), values=("□", format_size(record.size), format_date(record.modified), str(record.path)))

    @staticmethod
    def tier(size: int) -> str:
        if size >= 20 * 1024**3:
            return "Mortgage"
        if size >= 5 * 1024**3:
            return "Car"
        if size >= 1024**3:
            return "House"
        return "Appliances"

    @staticmethod
    def clear_tree(tree: ttk.Treeview) -> None:
        tree.delete(*tree.get_children())

    def clear_results(self) -> None:
        self.selected_delete.clear()
        self.clear_tree(self.summary_tree)
        self.clear_tree(self.large_tree)
        self.clear_tree(self.duplicate_tree)
        self.pause_event.clear()
        self.pause_button.configure(state="disabled", text="Pause")
        self.progress_var.set(0)
        self.scan_status.set("Ready · scanning is read-only")
        self.scan_detail.set("Choose a drive or folder to begin")

    def toggle_selected(self, tree: ttk.Treeview) -> None:
        for item in tree.selection():
            path = Path(item)
            if path in self.selected_delete:
                self.selected_delete.remove(path)
                tree.set(item, "select", "□")
            elif self.is_safe_candidate(path):
                self.selected_delete.add(path)
                tree.set(item, "select", "☑")

    def is_safe_candidate(self, path: Path) -> bool:
        resolved = safe_resolve(path) or path
        return not any(is_under(resolved, root) for root in PROTECTED_ROOTS)

    def clear_queue(self) -> None:
        self.selected_delete.clear()
        for tree in (self.large_tree, self.duplicate_tree):
            for item in tree.get_children():
                tree.set(item, "select", "□")

    def delete_selected(self) -> None:
        if not self.selected_delete:
            messagebox.showinfo(APP_NAME, "Select files in Large files or Duplicates first. Double-click a row to queue it.")
            return
        amount = len(self.selected_delete)
        if not messagebox.askyesno(APP_NAME, f"Move {amount} selected file(s) to the Recycle Bin?\n\nThis is the only action that changes files."):
            return
        failures = 0
        deleted = 0
        for path in list(self.selected_delete):
            if not self.is_safe_candidate(path):
                failures += 1
                continue
            try:
                import winshell  # Optional: only needed for Recycle Bin support.
                winshell.delete_file(str(path), no_confirm=True, allow_undo=True)
                deleted += 1
            except ImportError:
                messagebox.showwarning(APP_NAME, "Recycle Bin support needs optional package 'winshell'. No files were changed.\n\nInstall with: py -m pip install winshell")
                return
            except (OSError, PermissionError):
                failures += 1
        self.selected_delete.clear()
        self.scan_status.set(f"Queued deletion complete: {deleted} moved to Recycle Bin; {failures} skipped.")
        self.start_scan()


if __name__ == "__main__":
    app = CleanerApp()
    app.mainloop()
