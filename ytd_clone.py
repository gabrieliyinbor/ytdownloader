"""
YTD Clone Downloader
--------------------
A Tkinter front-end around yt-dlp with an optional FFmpeg conversion step.

Fixes vs. the original:
  * Correct yt-dlp format selection (bestvideo+bestaudio merge), so anything
    above 360p actually downloads.
  * FFmpeg auto-detection; tells you clearly if it's missing.
  * All UI updates funneled through a thread-safe queue (no Tk calls from
    worker threads).
  * Single source of truth for tab navigation, combo widgets, and quality
    parsing — removes the duplicated hero frames and labeled selects.
  * Cleaner progress hook that works even when info_dict is empty early on.
  * Failure states actually show what failed instead of resetting the row
    to the "Fetching info..." placeholder.
"""

import os
import sys
import re
import json
import shutil
import threading
import time
import queue
import subprocess
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_TITLE = "YTD Clone Downloader"
WINDOW_W, WINDOW_H = 1050, 680

BG      = "#e9e3d7"
PANEL   = "#f2f2f2"
WHITE   = "#ffffff"
GREEN   = "#29b300"
DARK    = "#2f2f2f"
BLUE    = "#1296f3"
TEXT    = "#111111"
MUTED   = "#666666"
BORDER  = "#cfcfcf"

QUALITY_OPTIONS = [
    "Best Available",
    "2160p 4K",
    "1440p 2K",
    "1080p Full HD",
    "720p HD",
    "480p Standard",
    "360p Medium (MP4)",
    "240p Low",
    "144p Very Low",
]

CONVERT_OPTIONS = [
    "iPad Video (MPEG-4 MP4)",
    "iPod Video (Apple QuickTime MOV)",
    "iPhone Video (MPEG-4 MP4)",
    "PSP Video (H.264 MP4)",
    "Cell Phone (H.263 3GP)",
    "Windows Media Video (V.7 WMV)",
    "XVid MPEG-4 Codec (AVI)",
    "MPEG Audio Layer 3 (MP3)",
]

CONVERSION_QUALITY = ["High", "Optimal", "Medium", "Low", "Same as original"]

# Extension mapping for the conversion step.
CONVERT_EXT = {
    "MPEG Audio Layer 3 (MP3)":              ".mp3",
    "iPod Video (Apple QuickTime MOV)":      ".mov",
    "Windows Media Video (V.7 WMV)":         ".wmv",
    "XVid MPEG-4 Codec (AVI)":               ".avi",
    "Cell Phone (H.263 3GP)":                ".3gp",
    "iPad Video (MPEG-4 MP4)":               ".mp4",
    "iPhone Video (MPEG-4 MP4)":             ".mp4",
    "PSP Video (H.264 MP4)":                 ".mp4",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bundle_dir():
    """Where PyInstaller extracts bundled files at runtime. Returns None
    when we're running from source."""
    return getattr(sys, "_MEIPASS", None)


def find_executable(name: str):
    """Look inside the PyInstaller bundle first (so a shipped .exe uses its
    bundled ffmpeg even if the tester's PATH doesn't have one), then fall
    back to whatever's on PATH."""
    bundle = _bundle_dir()
    if bundle:
        exe = name + (".exe" if sys.platform.startswith("win") else "")
        for sub in ("", "bin"):  # support both flat and bin/ layouts
            candidate = os.path.join(bundle, sub, exe) if sub else os.path.join(bundle, exe)
            if os.path.isfile(candidate):
                return candidate
    return shutil.which(name)


def quality_to_height(label: str):
    """'1080p Full HD' -> 1080, 'Best Available' -> None."""
    if label == "Best Available":
        return None
    first = label.split()[0]
    if first.endswith("p"):
        try:
            return int(first[:-1])
        except ValueError:
            return None
    return None


def format_string_for(height, have_ffmpeg=True):
    """
    Build a yt-dlp format string.

    The selector is a fallback chain separated by '/'. yt-dlp walks it
    left-to-right and picks the first rung that matches the formats YouTube
    actually offered for *this* video under *this* player client. If a rung
    is too strict (wrong container, no matching height), yt-dlp moves to the
    next. The final rung is the unconditional 'best' so we never end up
    with "Requested format is not available."
    """
    if not have_ffmpeg:
        # No merging possible — stick to single-file formats, and don't
        # demand mp4 specifically, since some videos only ship webm muxed.
        if height is None:
            return "best[ext=mp4]/best"
        return (
            f"best[height<={height}][ext=mp4]/"
            f"best[height<={height}]/"
            f"best"
        )

    # With ffmpeg: prefer merged bestvideo+bestaudio, but fall through to
    # any single-file format at the requested height, then any format at
    # all. Dropping the [ext=...] constraints here is the key fix for
    # "Requested format is not available" on videos where YouTube serves
    # the only audio track as webm/m4a and no mp4 muxed variant exists.
    if height is None:
        return "bestvideo+bestaudio/best"
    return (
        f"bestvideo[height<={height}]+bestaudio/"
        f"bestvideo[height<={height}]+bestaudio*/"
        f"best[height<={height}]/"
        f"bestvideo+bestaudio/"
        f"best"
    )


def human_size(n_bytes):
    if not n_bytes:
        return "--"
    mb = n_bytes / 1024 / 1024
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.2f} MB"


def human_speed(bps):
    if not bps:
        return "--"
    mb = bps / 1024 / 1024
    if mb >= 1:
        return f"{mb:.2f} MB/s"
    return f"{bps / 1024:.0f} KB/s"


# yt-dlp embeds ANSI color codes in some error messages ("\x1b[0;31mERROR:\x1b[0m ...").
# They render as garbage in a tkinter messagebox, so strip them before display.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def clean_error(text: str) -> str:
    return _ANSI_RE.sub("", str(text)).strip()


# ---------------------------------------------------------------------------
# Persistent user settings
# ---------------------------------------------------------------------------
def _config_dir() -> Path:
    """Per-user writable config folder. %APPDATA% on Windows, ~/.config on
    Linux, ~/Library/Application Support on macOS. Created if missing."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "YTDClone"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = _config_dir() / "settings.json"

DEFAULT_SETTINGS = {
    "save_dir": str(Path.home() / "Downloads"),
    "quality": "Best Available",
    "subtitles": False,
    "auto_convert": False,
    "convert_to": "iPad Video (MPEG-4 MP4)",
    "delete_original": False,
    "convert_save_dir": str(Path.home() / "Downloads"),
    "last_update_check": 0,         # epoch seconds of last yt-dlp version check
    "last_known_ytdlp": "",         # version reported by GitHub last time we checked
}


def load_settings() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Fill in any keys added since the file was written.
        return {**DEFAULT_SETTINGS, **data}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(data: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # don't crash the app over a failed settings write


# ---------------------------------------------------------------------------
# yt-dlp update checker
# ---------------------------------------------------------------------------
GITHUB_LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
UPDATE_CHECK_INTERVAL = 24 * 60 * 60  # once per day is plenty


def check_ytdlp_update(current_version: str, timeout: float = 4.0):
    """Hit GitHub's releases API and return the latest tag name if it
    differs from our version, else None. Fully silent on any failure —
    this runs in a background thread and must never raise into the UI."""
    try:
        req = urllib.request.Request(
            GITHUB_LATEST,
            headers={"User-Agent": "ytd-clone-update-check"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = (data.get("tag_name") or "").lstrip("v").strip()
        if latest and latest != current_version:
            return latest
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------
class SidebarButton(tk.Frame):
    def __init__(self, master, text, command, active=False):
        super().__init__(master, bg=master["bg"], height=62)
        self.command = command
        self.active = active
        self.pack_propagate(False)

        self.inner = tk.Frame(self, bg=BLUE if active else master["bg"])
        self.inner.pack(fill="both", expand=True)

        self.label = tk.Label(
            self.inner,
            text=text,
            bg=BLUE if active else master["bg"],
            fg="white" if active else TEXT,
            font=("Arial", 10, "bold" if active else "normal"),
            cursor="hand2",
        )
        self.label.pack(expand=True)

        for w in (self.label, self.inner):
            w.bind("<Button-1>", lambda e: self.command())

    def set_active(self, is_active: bool):
        self.active = is_active
        bg = BLUE if is_active else self.master["bg"]
        fg = "white" if is_active else TEXT
        weight = "bold" if is_active else "normal"
        self.inner.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg, font=("Arial", 10, weight))


class LabeledSelect(tk.Frame):
    def __init__(self, master, label, values, width=52):
        super().__init__(master, bg=WHITE)
        tk.Label(self, text=label, bg=WHITE, fg=TEXT,
                 font=("Arial", 10), width=18, anchor="w").pack(side="left")
        self.var = tk.StringVar(value=values[0])
        self.combo = ttk.Combobox(
            self, textvariable=self.var, values=values,
            width=width, state="readonly",
        )
        self.combo.pack(side="left", fill="x", expand=True)

    def get(self):
        return self.var.get()

    def set(self, value):
        self.var.set(value)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.minsize(980, 620)
        self.configure(bg=BG)

        self.settings = load_settings()
        self.save_dir = self.settings["save_dir"]
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except OSError:
            # The saved folder no longer exists (e.g. external drive).
            # Fall back to ~/Downloads rather than refusing to launch.
            self.save_dir = str(Path.home() / "Downloads")
            os.makedirs(self.save_dir, exist_ok=True)

        self.downloaded_files = {}       # tree row id -> final file path
        self.msg_queue = queue.Queue()
        self.ffmpeg_path = find_executable("ffmpeg")
        self._update_title()

        self._build_styles()
        self._build_layout()
        self.show_tab("download")

        if yt_dlp is None:
            self.after(200, lambda: messagebox.showwarning(
                "Missing package",
                "yt-dlp is not installed.\n\nRun:\n    pip install -U yt-dlp",
            ))
        else:
            # Background update check (non-blocking, rate-limited to once/day).
            self._maybe_check_for_updates()

        # Save any settings changes when the user closes the window.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._poll_queue()

    def _maybe_check_for_updates(self):
        """Hit the GitHub API at most once every UPDATE_CHECK_INTERVAL to
        see if there's a newer yt-dlp. All network work happens on a
        daemon thread; the result comes back through the UI queue."""
        now = time.time()
        if now - self.settings.get("last_update_check", 0) < UPDATE_CHECK_INTERVAL:
            return
        self.settings["last_update_check"] = now
        current = getattr(yt_dlp.version, "__version__", "")

        def worker():
            latest = check_ytdlp_update(current)
            if latest and latest != self.settings.get("last_known_ytdlp"):
                self.settings["last_known_ytdlp"] = latest
                self.msg_queue.put(("update_available", current, latest))

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self):
        # Snapshot whatever the user has typed/selected into settings.
        try:
            self.settings.update({
                "save_dir": self.save_var.get().strip() or self.save_dir,
                "quality": self.download_quality.get(),
                "subtitles": bool(self.subtitles_var.get()),
                "auto_convert": bool(self.auto_convert_var.get()),
                "convert_to": self.convert_to.get(),
                "delete_original": bool(self.delete_original_after_convert.get()),
                "convert_save_dir": self.convert_save_var.get().strip(),
            })
        except (tk.TclError, AttributeError):
            pass  # widgets might already be torn down
        save_settings(self.settings)
        self.destroy()

    # ----- styling -----
    def _build_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground="white", background="white")
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))

    # ----- layout -----
    def _build_layout(self):
        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True)

        # Sidebar
        self.sidebar = tk.Frame(root, bg=PANEL, width=120)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="YTD", bg=PANEL, fg=TEXT,
                 font=("Arial", 15, "bold")).pack(pady=(12, 18))

        self.tab_buttons = {}
        for key, label in (("download", "Download"),
                           ("convert",  "Convert"),
                           ("activity", "Activity")):
            btn = SidebarButton(self.sidebar, label,
                                lambda k=key: self.show_tab(k))
            btn.pack(fill="x", pady=4)
            self.tab_buttons[key] = btn

        tk.Frame(self.sidebar, bg=PANEL).pack(expand=True, fill="both")

        SidebarButton(self.sidebar, "Settings",
                      self.show_settings).pack(fill="x", pady=(8, 14))

        # Content area
        self.content = tk.Frame(root, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

        tk.Frame(self.content, bg=BG, height=20).pack(fill="x")
        self.tab_holder = tk.Frame(self.content, bg=BG)
        self.tab_holder.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        self.tabs = {
            "download": self._build_download_tab(self.tab_holder),
            "convert":  self._build_convert_tab(self.tab_holder),
            "activity": self._build_activity_tab(self.tab_holder),
        }

    def show_tab(self, name: str):
        for frame in self.tabs.values():
            frame.pack_forget()
        self.tabs[name].pack(fill="both", expand=True)
        for key, btn in self.tab_buttons.items():
            btn.set_active(key == name)

    # ----- tab builders -----
    def _hero(self, master):
        hero = tk.Frame(master, bg="#efefef", height=100)
        hero.pack(fill="x", padx=6, pady=(0, 18))
        hero.pack_propagate(False)
        return hero

    def _build_download_tab(self, master):
        frame = tk.Frame(master, bg=BG)
        self._hero(frame)

        section = tk.Frame(frame, bg=BG)
        section.pack(fill="both", expand=True)

        tk.Label(section, text="Enter the URL of the video you want to download",
                 bg=BG, fg=TEXT, font=("Arial", 10)).pack(anchor="w", padx=6)

        url_row = tk.Frame(section, bg=BG)
        url_row.pack(fill="x", padx=6, pady=(6, 10))
        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(url_row, textvariable=self.url_var,
                                  font=("Arial", 12), relief="solid", bd=1)
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=8)
        tk.Button(url_row, text="📋", width=4, relief="flat", bg="#ededed",
                  command=self.paste_clipboard).pack(side="left", padx=(6, 0))

        banner = tk.Frame(section, bg=GREEN, height=26)
        banner.pack(fill="x", padx=6)
        banner.pack_propagate(False)
        tk.Label(banner,
                 text="Upgrade now!    Go Premium to download multiple videos at once!",
                 bg=GREEN, fg="white",
                 font=("Arial", 10, "bold")).pack(side="left", padx=10)

        prefs = tk.Frame(section, bg=WHITE, highlightbackground=BORDER,
                         highlightthickness=1)
        prefs.pack(fill="x", padx=6, pady=(14, 0))

        tk.Label(prefs, text="Preferences", bg=WHITE, fg=TEXT,
                 font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 6))

        inner = tk.Frame(prefs, bg=WHITE)
        inner.pack(fill="x", padx=18, pady=(6, 14))

        self.download_quality = LabeledSelect(inner, "Download quality", QUALITY_OPTIONS)
        self.download_quality.pack(fill="x", pady=8)
        if self.settings["quality"] in QUALITY_OPTIONS:
            self.download_quality.set(self.settings["quality"])

        self.subtitles_var = tk.BooleanVar(value=self.settings["subtitles"])
        tk.Checkbutton(inner, text="Automatically download subtitles",
                       variable=self.subtitles_var, bg=WHITE,
                       activebackground=WHITE).pack(anchor="w", padx=(125, 0), pady=(0, 8))

        save_row = tk.Frame(inner, bg=WHITE)
        save_row.pack(fill="x", pady=8)
        tk.Label(save_row, text="Save to", bg=WHITE, fg=TEXT,
                 font=("Arial", 10), width=18, anchor="w").pack(side="left")
        tk.Button(save_row, text="📁", width=3, command=self.pick_save_dir).pack(side="left", padx=(0, 10))
        self.save_var = tk.StringVar(value=self.save_dir)
        tk.Entry(save_row, textvariable=self.save_var, relief="flat",
                 bg=WHITE, fg="#3778c2", font=("Arial", 10)
                 ).pack(side="left", fill="x", expand=True)

        self.auto_convert_var = tk.BooleanVar(value=self.settings["auto_convert"])
        tk.Checkbutton(inner, text="Automatically convert after download",
                       variable=self.auto_convert_var, bg=WHITE,
                       activebackground=WHITE).pack(anchor="w", padx=(125, 0), pady=(10, 6))

        self.convert_to = LabeledSelect(inner, "Convert to", CONVERT_OPTIONS)
        self.convert_to.pack(fill="x", pady=6)
        if self.settings["convert_to"] in CONVERT_OPTIONS:
            self.convert_to.set(self.settings["convert_to"])
        self.convert_quality = LabeledSelect(inner, "Conversion quality", CONVERSION_QUALITY)
        self.convert_quality.pack(fill="x", pady=6)

        self.delete_original_after_convert = tk.BooleanVar(value=self.settings["delete_original"])
        tk.Checkbutton(inner, text="Delete original file after conversion",
                       variable=self.delete_original_after_convert,
                       bg=WHITE, activebackground=WHITE
                       ).pack(anchor="w", padx=(125, 0), pady=(4, 0))

        foot = tk.Frame(section, bg=BG)
        foot.pack(fill="x", padx=6, pady=12)
        tk.Button(foot, text="⬇ DOWNLOAD", bg=BLUE, fg="white",
                  relief="flat", font=("Arial", 12, "bold"),
                  padx=22, pady=8,
                  command=self.start_download).pack(side="right")

        return frame

    def _build_convert_tab(self, master):
        frame = tk.Frame(master, bg=BG)
        self._hero(frame)

        outer = tk.Frame(frame, bg=WHITE, highlightbackground=BORDER,
                         highlightthickness=1)
        outer.pack(fill="both", expand=True, padx=6)

        top = tk.Frame(outer, bg=WHITE)
        top.pack(fill="x", padx=14, pady=(14, 10))
        tk.Label(top, text="Select the video file", bg=WHITE, fg=TEXT,
                 font=("Arial", 10, "bold")).pack(anchor="w")

        browse = tk.Frame(top, bg=WHITE)
        browse.pack(fill="x", pady=(10, 0))
        tk.Button(browse, text="📁 Browse", command=self.pick_convert_file).pack(side="left")
        self.convert_file_var = tk.StringVar(value="No file selected")
        self.selected_convert_file = None
        tk.Label(browse, textvariable=self.convert_file_var,
                 bg=WHITE, fg=MUTED).pack(side="left", padx=12)

        ttk.Separator(outer).pack(fill="x", padx=14, pady=8)
        tk.Label(outer, text="Conversion Preferences", bg=WHITE, fg=TEXT,
                 font=("Arial", 10, "bold")).pack(anchor="w", padx=14, pady=(6, 6))

        inner = tk.Frame(outer, bg=WHITE)
        inner.pack(fill="x", padx=18, pady=(0, 12))

        self.convert_mode2 = LabeledSelect(inner, "Convert video to", CONVERT_OPTIONS)
        self.convert_mode2.pack(fill="x", pady=6)
        self.convert_quality2 = LabeledSelect(inner, "Conversion quality", CONVERSION_QUALITY)
        self.convert_quality2.pack(fill="x", pady=6)

        self.delete_after_convert2 = tk.BooleanVar(value=False)
        tk.Checkbutton(inner, text="Delete original file after conversion",
                       variable=self.delete_after_convert2, bg=WHITE,
                       activebackground=WHITE
                       ).pack(anchor="w", padx=(125, 0), pady=(0, 8))

        save_row = tk.Frame(inner, bg=WHITE)
        save_row.pack(fill="x", pady=8)
        tk.Label(save_row, text="Save to", bg=WHITE, fg=TEXT, width=18,
                 anchor="w").pack(side="left")
        tk.Button(save_row, text="📁", width=3,
                  command=self.pick_convert_save_dir).pack(side="left", padx=(0, 10))
        self.convert_save_var = tk.StringVar(value=self.settings.get("convert_save_dir") or self.save_dir)
        tk.Entry(save_row, textvariable=self.convert_save_var, relief="flat",
                 bg=WHITE, fg="#3778c2").pack(side="left", fill="x", expand=True)

        self.same_as_downloads = tk.BooleanVar(value=False)
        tk.Checkbutton(inner, text="Same as downloads",
                       variable=self.same_as_downloads, bg=WHITE,
                       activebackground=WHITE
                       ).pack(anchor="w", padx=(125, 0), pady=(0, 12))

        bottom = tk.Frame(frame, bg=BG)
        bottom.pack(fill="x", padx=6, pady=12)
        tk.Button(bottom, text="⟳ CONVERT", bg=BLUE, fg="white",
                  relief="flat", font=("Arial", 12, "bold"),
                  padx=22, pady=8,
                  command=self.convert_selected_file).pack(side="right")

        return frame

    def _build_activity_tab(self, master):
        frame = tk.Frame(master, bg=BG)

        wrapper = tk.Frame(frame, bg=WHITE, highlightbackground=BORDER,
                           highlightthickness=1)
        wrapper.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        tk.Label(wrapper, text="Download Activity", bg=WHITE, fg=TEXT,
                 font=("Arial", 11, "bold")
                 ).pack(anchor="w", padx=12, pady=(12, 8))

        columns = ("video", "size", "progress", "speed", "status", "eta")
        self.tree = ttk.Treeview(wrapper, columns=columns, show="headings")
        for col, text, width in (
            ("video",    "Video",    340),
            ("size",     "Size",     100),
            ("progress", "Progress", 100),
            ("speed",    "Speed",    110),
            ("status",   "Status",   120),
            ("eta",      "ETA",      100),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # right-click (Windows/Linux) and Ctrl-click (macOS)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Button-2>", self._show_context_menu)

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Open file", command=self.open_selected_file)
        self.context_menu.add_command(label="Open containing folder", command=self.open_selected_folder)
        self.context_menu.add_command(label="Remove from activity", command=self.remove_selected_activity)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Copy video title", command=self.copy_selected_title)

        return frame

    # ----- folder / file pickers -----
    def paste_clipboard(self):
        try:
            self.url_var.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def pick_save_dir(self):
        path = filedialog.askdirectory(initialdir=self.save_var.get() or self.save_dir)
        if path:
            self.save_var.set(path)
            self.save_dir = path

    def pick_convert_save_dir(self):
        path = filedialog.askdirectory(initialdir=self.convert_save_var.get() or self.save_dir)
        if path:
            self.convert_save_var.set(path)

    def pick_convert_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Video / audio", "*.mp4 *.mkv *.webm *.mov *.avi *.m4a *.mp3"),
                       ("All files", "*.*")],
        )
        if path:
            self.selected_convert_file = path
            self.convert_file_var.set(path)

    # ----- download -----
    def start_download(self):
        if yt_dlp is None:
            messagebox.showerror("Missing package",
                                 "yt-dlp is not installed.\n\nRun:\n    pip install -U yt-dlp")
            return

        # Re-detect in case ffmpeg was installed while the app was running.
        self._refresh_ffmpeg()

        url = self.url_var.get().strip()
        save_dir = self.save_var.get().strip()
        quality = self.download_quality.get()

        if not url:
            messagebox.showerror("Missing URL", "Paste a video URL first.")
            return
        if not save_dir:
            messagebox.showerror("Missing folder", "Choose where to save the file.")
            return
        if not os.path.isdir(save_dir):
            messagebox.showerror("Invalid folder", "The save folder does not exist.")
            return

        # Warn once if they're asking for HD+ without ffmpeg. YouTube (and
        # many other sites) only ships muxed video+audio up to 360p — every
        # higher quality is delivered as separate streams that ffmpeg has
        # to merge. Without ffmpeg we CANNOT get HD with sound from YouTube.
        height = quality_to_height(quality)
        if (height is None or height > 360) and not self.ffmpeg_path:
            cont = messagebox.askyesno(
                "FFmpeg required for HD with sound",
                "FFmpeg isn't installed.\n\n"
                "On YouTube, only 360p comes as a single file with audio. "
                "Anything higher (720p, 1080p, 4K) is delivered as separate "
                "video and audio streams that FFmpeg has to merge.\n\n"
                "Without FFmpeg this download will be capped at 360p "
                "(higher qualities would be video-only, no sound).\n\n"
                "Install FFmpeg (winget install Gyan.FFmpeg) for full HD.\n\n"
                "Continue at 360p anyway?",
            )
            if not cont:
                return

        item_id = self.tree.insert(
            "", "end",
            values=("Fetching info...", "-", "0%", "-", "Queued", "-"),
        )
        self.show_tab("activity")

        threading.Thread(
            target=self._download_worker,
            args=(url, save_dir, quality, item_id),
            daemon=True,
        ).start()

    def _download_worker(self, url, save_dir, quality, item_id):
        started = time.time()
        height = quality_to_height(quality)
        fmt = format_string_for(height, have_ffmpeg=bool(self.ffmpeg_path))

        def progress_hook(d):
            status = d.get("status")
            info = d.get("info_dict") or {}
            title = (info.get("title")
                     or (Path(d["filename"]).stem if d.get("filename") else "Downloading..."))

            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                pct = f"{downloaded / total * 100:.0f}%" if total else "--"
                eta = d.get("eta")
                eta_str = f"{int(eta)}s" if eta is not None else "--"
                self.msg_queue.put(("update_row", item_id, (
                    title, human_size(total), pct,
                    human_speed(d.get("speed") or 0),
                    "Downloading", eta_str,
                )))

            elif status == "finished":
                # Download of one stream is done; merging/post-processing next.
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                self.msg_queue.put(("update_row", item_id, (
                    title, human_size(total), "100%", "--",
                    "Processing", "0s",
                )))

        outtmpl = os.path.join(save_dir, "%(title)s.%(ext)s")

        opts = {
            "outtmpl": outtmpl,
            "format": fmt,
            "noplaylist": True,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "windowsfilenames": sys.platform.startswith("win"),
            "overwrites": True,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": 4,  # faster on segmented streams
            # YouTube anti-bot workaround: yt-dlp tries these internal
            # clients in order. Each exposes a slightly different format
            # menu. Listing several makes "Requested format is not
            # available" and "HTTP 403" much rarer — if one client can't
            # serve it, the next usually can.
            "extractor_args": {
                "youtube": {"player_client": ["android", "ios", "web", "tv_embedded"]},
            },
        }
        if self.ffmpeg_path:
            opts["ffmpeg_location"] = self.ffmpeg_path
            opts["merge_output_format"] = "mp4"
        if self.subtitles_var.get():
            opts.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["en"],
            })

        # Optional cookie jar from a local browser. This handles videos that
        # require age-confirmation, region checks, or membership. It's opt-in
        # via self.cookies_browser (set in show_settings / future UI).
        browser = getattr(self, "cookies_browser", None)
        if browser:
            opts["cookiesfrombrowser"] = (browser,)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                final_path = ydl.prepare_filename(info)

                # After merging, the extension may have changed (e.g. .webm -> .mp4).
                if not os.path.exists(final_path):
                    stem = os.path.splitext(final_path)[0]
                    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3"):
                        cand = stem + ext
                        if os.path.exists(cand):
                            final_path = cand
                            break

                if not os.path.exists(final_path):
                    raise FileNotFoundError("Download finished but output file was not found.")
                if os.path.getsize(final_path) == 0:
                    raise RuntimeError("Downloaded file is empty.")

                elapsed = max(time.time() - started, 0.1)
                size_bytes = os.path.getsize(final_path)
                avg_speed = human_speed(size_bytes / elapsed)
                title = info.get("title", Path(final_path).stem)

                self.downloaded_files[item_id] = final_path
                self.msg_queue.put(("update_row", item_id, (
                    title, human_size(size_bytes), "100%", avg_speed,
                    "Completed", "0s",
                )))
                self.msg_queue.put(("toast", "Download finished ✅"))

                if self.auto_convert_var.get():
                    self.msg_queue.put((
                        "convert_after_download",
                        final_path,
                        self.convert_to.get(),
                        self.delete_original_after_convert.get(),
                    ))

        except Exception as e:
            # Keep whatever title we managed to fetch, but mark as Failed
            # so the user can see what happened in the row.
            current = self.tree.item(item_id, "values") if self.tree.exists(item_id) else None
            title = current[0] if current and current[0] != "Fetching info..." else "Failed download"
            self.msg_queue.put(("update_row", item_id, (
                title, "-", "--", "-", "Failed", "-",
            )))
            err = clean_error(e)
            if "403" in err or "Forbidden" in err:
                err += ("\n\nThis is usually YouTube's anti-bot check.\n"
                        "Try:\n"
                        "  • Update yt-dlp: pip install -U yt-dlp\n"
                        "  • A different video (some are geo/age-locked)\n"
                        "  • Sign into YouTube in your browser, then retry")
            elif "Requested format is not available" in err:
                err += ("\n\nYouTube didn't offer a stream matching your "
                        "quality choice for this video.\n"
                        "Try:\n"
                        "  • Pick a different quality (e.g. Best Available)\n"
                        "  • Update yt-dlp: pip install -U yt-dlp")
            self.msg_queue.put(("toast", f"Download failed:\n{err}"))

    # ----- conversion -----
    def convert_selected_file(self):
        path = self.selected_convert_file
        if not path:
            messagebox.showerror("No file", "Choose a file to convert first.")
            return
        if not os.path.exists(path):
            messagebox.showerror("Missing file", "That file no longer exists.")
            return
        output_dir = self.save_dir if self.same_as_downloads.get() else self.convert_save_var.get().strip()
        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showerror("Invalid folder", "Choose a valid save folder first.")
            return
        self._run_conversion(path, self.convert_mode2.get(),
                             output_dir, self.delete_after_convert2.get())

    def _run_conversion(self, input_path, target, output_dir, delete_original):
        if not self.ffmpeg_path:
            messagebox.showerror(
                "FFmpeg missing",
                "Install FFmpeg and add it to PATH to use conversion.\n\n"
                "macOS:   brew install ffmpeg\n"
                "Windows: https://www.gyan.dev/ffmpeg/builds/\n"
                "Linux:   sudo apt install ffmpeg",
            )
            return

        ext = CONVERT_EXT.get(target, ".mp4")
        output_path = os.path.join(output_dir, f"{Path(input_path).stem}_converted{ext}")

        cmd = [self.ffmpeg_path, "-y", "-i", input_path]
        if ext == ".mp3":
            cmd += ["-vn", "-ab", "192k", output_path]
        else:
            cmd += [output_path]

        def worker():
            try:
                subprocess.run(cmd, check=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
                if delete_original and os.path.exists(input_path):
                    try:
                        os.remove(input_path)
                    except OSError:
                        pass
                self.msg_queue.put(("toast", f"Conversion finished ✅\nSaved to: {output_path}"))
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode(errors="replace").strip().splitlines()
                tail = "\n".join(stderr[-4:]) if stderr else str(e)
                self.msg_queue.put(("toast", f"Conversion failed:\n{tail}"))
            except Exception as e:
                self.msg_queue.put(("toast", f"Conversion failed:\n{e}"))

        threading.Thread(target=worker, daemon=True).start()

    # ----- activity context menu actions -----
    def _show_context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def _selected_row(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _open_path(self, path):
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)

    def open_selected_file(self):
        row = self._selected_row()
        path = self.downloaded_files.get(row) if row else None
        if path and os.path.exists(path):
            self._open_path(path)

    def open_selected_folder(self):
        row = self._selected_row()
        path = self.downloaded_files.get(row) if row else None
        if path and os.path.exists(path):
            self._open_path(os.path.dirname(path))

    def remove_selected_activity(self):
        row = self._selected_row()
        if row:
            self.tree.delete(row)
            self.downloaded_files.pop(row, None)

    def copy_selected_title(self):
        row = self._selected_row()
        if not row:
            return
        values = self.tree.item(row, "values")
        if values:
            self.clipboard_clear()
            self.clipboard_append(values[0])
            self.update()

    # ----- misc -----
    def _update_title(self):
        """Show ffmpeg detection status in the title bar so it's obvious
        whether HD downloads will work."""
        tag = "✓ ffmpeg" if self.ffmpeg_path else "✗ no ffmpeg"
        self.title(f"{APP_TITLE}  —  {tag}")

    def _refresh_ffmpeg(self):
        """Re-run detection. Lets the user install ffmpeg while the app is
        open and just hit Download again instead of restarting."""
        self.ffmpeg_path = find_executable("ffmpeg")
        self._update_title()

    def show_settings(self):
        self._refresh_ffmpeg()
        ff = self.ffmpeg_path or "Not found on PATH"
        yd = f"v{yt_dlp.version.__version__}" if yt_dlp else "NOT installed (pip install -U yt-dlp)"
        messagebox.showinfo(
            "Settings",
            f"yt-dlp:  {yd}\n"
            f"ffmpeg:  {ff}\n\n"
            "Quick setup:\n"
            "  1. pip install -U yt-dlp\n"
            "  2. Install FFmpeg (needed for HD merging and conversion)\n"
            "  3. Paste a video URL and hit Download.\n\n"
            "If you installed ffmpeg but it still says 'Not found', fully\n"
            "restart this app — it can't see PATH changes that happened\n"
            "after its parent process started.",
        )

    # ----- thread-safe UI pump -----
    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "update_row":
                    _, item_id, values = msg
                    if self.tree.exists(item_id):
                        self.tree.item(item_id, values=values)
                elif kind == "toast":
                    messagebox.showinfo(APP_TITLE, msg[1])
                elif kind == "convert_after_download":
                    _, path, target, delete_original = msg
                    self._run_conversion(path, target, os.path.dirname(path), delete_original)
                elif kind == "update_available":
                    _, current, latest = msg
                    # Show a subtle banner in the title bar rather than a
                    # blocking dialog — testers shouldn't have to click OK
                    # on a popup every day.
                    self.title(f"{APP_TITLE}  —  "
                               f"{'✓ ffmpeg' if self.ffmpeg_path else '✗ no ffmpeg'}"
                               f"  —  yt-dlp {latest} available (current {current})")
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    App().mainloop()
