#!/usr/bin/env python3
"""
Waypaper-Y v1.2 – wallpaper manager for Wayland compositors
Supports: Hyprland, Sway, Niri (I HOPE) (and any WM using swww / swaybg / wbg)
Config: ~/.config/waypapery/config.ini
"""

import os
import subprocess
import time
import json
import shutil
import hashlib
import configparser
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib, Gio
from PIL import Image
import io

# ──────────────────────────────────────────── Paths ─────────────
CONFIG_DIR     = Path.home() / ".config" / "waypapery"
CONFIG_FILE    = CONFIG_DIR / "config.ini"
AUTOSTART_CONF = CONFIG_DIR / "autostart.conf"
CACHE_DIR      = CONFIG_DIR / "cache"
DYNAMIC_DAEMON = "wpydynamic"

THUMB_SIZE       = 148
COOLDOWN_SECONDS = 2.3

# ──────────────────────────────────────────── Compositor ────────

def detect_compositor() -> str:
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    if os.environ.get("NIRI_SOCKET"):
        return "niri"
    return "unknown"


def list_monitors(compositor: str) -> list[str]:
    try:
        if compositor == "hyprland":
            out = subprocess.check_output(
                ["hyprctl", "monitors", "-j"], timeout=3, stderr=subprocess.DEVNULL
            )
            return [m["name"] for m in json.loads(out)]
        elif compositor == "sway":
            out = subprocess.check_output(
                ["swaymsg", "-t", "get_outputs"], timeout=3, stderr=subprocess.DEVNULL
            )
            return [o["name"] for o in json.loads(out) if o.get("active")]
        elif compositor == "niri":
            out = subprocess.check_output(
                ["niri", "msg", "--json", "outputs"], timeout=3, stderr=subprocess.DEVNULL
            )
            return list(json.loads(out).keys())
    except Exception:
        pass
    return ["*"]


def compositor_config_paths(compositor: str) -> list[Path]:
    h = Path.home()
    if compositor == "hyprland":
        hypr = h / ".config" / "hypr"
        if hypr.is_dir():
            candidates = sorted(hypr.glob("*.conf"))
            preferred = hypr / "hyprland.conf"
            if preferred in candidates:
                candidates = [preferred] + [c for c in candidates if c != preferred]
            return candidates
        return []
    elif compositor == "sway":
        return [h / ".config" / "sway" / "config", h / ".sway" / "config"]
    elif compositor == "niri":
        return [h / ".config" / "niri" / "config.kdl"]
    return []


def source_line_for(compositor: str) -> str:
    if compositor == "hyprland":
        return f"source = {AUTOSTART_CONF}"
    elif compositor == "sway":
        return f"include {AUTOSTART_CONF}"
    return ""  # niri handled separately


def ensure_compositor_sources_autostart(compositor: str) -> bool:
    """Inject a source/include line into the compositor config if not already there."""
    src_line = source_line_for(compositor)
    if not src_line:
        return _inject_niri_autostart()

    for conf_path in compositor_config_paths(compositor):
        if not conf_path.is_file():
            continue
        try:
            content = conf_path.read_text(encoding="utf-8")
            if str(AUTOSTART_CONF) in content:
                return True
            content += f"\n# Added by Waypaper-Y\n{src_line}\n"
            conf_path.write_text(content, encoding="utf-8")
            return True
        except OSError:
            continue
    return False


def _inject_niri_autostart() -> bool:
    line = f'spawn-at-startup "{DYNAMIC_DAEMON}";'
    for conf in compositor_config_paths("niri"):
        if not conf.is_file():
            continue
        try:
            content = conf.read_text(encoding="utf-8")
            if DYNAMIC_DAEMON in content:
                return True
            content += f"\n// Added by Waypaper-Y\n{line}\n"
            conf.write_text(content, encoding="utf-8")
            return True
        except OSError:
            continue
    return False


# ──────────────────────────────────────────── Autostart ─────────
#
# autostart.conf contains ONE exec line that runs on login.
# Two modes:
#   dynamic  → exec wpydynamic
#   static   → exec wpydynamic --restore
#
# The compositor config sources this file once (injected on first use).

def _exec_line(compositor: str, args: str = "") -> str:
    cmd = f"{DYNAMIC_DAEMON} {args}".strip()
    if compositor == "hyprland":
        return f"exec-once = {cmd}"
    elif compositor == "sway":
        return f"exec {cmd}"
    elif compositor == "niri":
        return f'spawn-at-startup "{cmd}"'
    return f"exec {cmd}"


def write_autostart(compositor: str, mode: str):
    """
    Write autostart.conf for the given mode.
      mode='dynamic'  → runs wpydynamic (daemon)
      mode='static'   → runs wpydynamic --restore (one-shot, sets last wallpaper)
      mode='off'      → comments out the line
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if mode == "dynamic":
        line = _exec_line(compositor)
    elif mode == "static":
        line = _exec_line(compositor, "--restore")
    else:
        line = f"# {_exec_line(compositor)}  # disabled by Waypaper-Y"

    AUTOSTART_CONF.write_text(line + "\n", encoding="utf-8")
    # make sure compositor is sourcing this file
    ensure_compositor_sources_autostart(compositor)


def autostart_mode_active() -> bool:
    """Returns True if autostart.conf has an uncommented line."""
    if not AUTOSTART_CONF.is_file():
        return False
    try:
        content = AUTOSTART_CONF.read_text(encoding="utf-8").strip()
        return bool(content) and not content.startswith("#")
    except OSError:
        return False


# ──────────────────────────────────────────── Config ────────────

DEFAULT_CONFIG = {
    "waypapery": {
        "wallpaper_dir": str(Path.home() / "Pictures" / "Wallpapers"),
        "backend":        "swww",
        "monitor":        "*",
        "min_interval":   "300",
        "max_interval":   "600",
        "last_wallpaper": "",
        "mode":           "static",
        "cache_enabled":  "true",
        "cols_per_row":   "5",
    }
}

ALL_BACKENDS = ["hyprpaper", "swww", "swaybg", "wbg"]


class Config:
    def __init__(self):
        self._cfg = configparser.ConfigParser()
        self._ensure_defaults()

    def _ensure_defaults(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.is_file():
            self._cfg.read(CONFIG_FILE, encoding="utf-8")
        for section, values in DEFAULT_CONFIG.items():
            if not self._cfg.has_section(section):
                self._cfg.add_section(section)
            for key, val in values.items():
                if not self._cfg.has_option(section, key):
                    self._cfg.set(section, key, val)
        self._save()

    def _save(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            self._cfg.write(f)

    def get(self, key: str, fallback: str = "") -> str:
        return self._cfg.get("waypapery", key, fallback=fallback)

    def set(self, key: str, value: str):
        self._cfg.set("waypapery", key, value)
        self._save()

    @property
    def wallpaper_dir(self) -> Path:
        return Path(self.get("wallpaper_dir", str(Path.home() / "Pictures" / "Wallpapers")))

    @property
    def backend(self) -> str:
        return self.get("backend", "swww")

    @backend.setter
    def backend(self, v: str):
        self.set("backend", v)

    @property
    def monitor(self) -> str:
        return self.get("monitor", "*")

    @monitor.setter
    def monitor(self, v: str):
        self.set("monitor", v)

    @property
    def min_interval(self) -> int:
        return int(self.get("min_interval", "300"))

    @property
    def max_interval(self) -> int:
        return int(self.get("max_interval", "600"))

    @property
    def last_wallpaper(self) -> str:
        return self.get("last_wallpaper", "")

    @last_wallpaper.setter
    def last_wallpaper(self, v: str):
        self.set("last_wallpaper", v)

    @property
    def mode(self) -> str:
        return self.get("mode", "static")

    @mode.setter
    def mode(self, v: str):
        self.set("mode", v)

    @property
    def cache_enabled(self) -> bool:
        return self.get("cache_enabled", "true").lower() in ("true", "1", "yes")

    @cache_enabled.setter
    def cache_enabled(self, v: bool):
        self.set("cache_enabled", "true" if v else "false")

    @property
    def cols_per_row(self) -> int:
        return max(1, int(self.get("cols_per_row", "5")))

    @cols_per_row.setter
    def cols_per_row(self, v: int):
        self.set("cols_per_row", str(v))


# ──────────────────────────────────────────── Thumbnail Cache ───

def _thumb_cache_path(image_path: Path) -> Path:
    """Return the cache file path for a given wallpaper image."""
    stat = image_path.stat()
    key = f"{image_path}:{stat.st_size}:{stat.st_mtime}"
    digest = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"{digest}.png"


def load_thumbnail_cached(image_path: Path) -> bytes:
    """
    Return PNG bytes for a thumbnail.
    Reads from cache if available and fresh; otherwise generates and caches.
    """
    cache_file = _thumb_cache_path(image_path)
    if cache_file.is_file():
        return cache_file.read_bytes()

    # Generate thumbnail
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()

    # Write to cache
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(data)
    except OSError:
        pass

    return data


def load_thumbnail_uncached(image_path: Path) -> bytes:
    """Generate thumbnail bytes without touching the cache."""
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def clear_thumbnail_cache() -> int:
    """Delete all cached thumbnails. Returns number of files removed."""
    if not CACHE_DIR.is_dir():
        return 0
    count = 0
    for f in CACHE_DIR.glob("*.png"):
        try:
            f.unlink()
            count += 1
        except OSError:
            pass
    return count


# ──────────────────────────────────────────── Backends ──────────

def resolve_hyprpaper_monitor(monitor: str) -> str:
    """Return a real monitor name for hyprpaper — auto-detect if config says * or empty."""
    if monitor not in ("*", ""):
        return monitor
    try:
        out = subprocess.check_output(
            ["hyprctl", "monitors", "-j"], timeout=3, stderr=subprocess.DEVNULL
        )
        data = json.loads(out)
        if data:
            return data[0]["name"]
    except Exception:
        pass
    return monitor


def apply_wallpaper(backend: str, path: str, monitor: str) -> None:
    """Apply wallpaper using the chosen backend. Raises on failure."""
    mon = monitor if monitor not in ("*", "") else ""

    if backend == "hyprpaper":
        # hyprpaper has no preload command — just set wallpaper directly
        real_mon = resolve_hyprpaper_monitor(monitor)
        subprocess.run(
            ["hyprctl", "hyprpaper", "wallpaper", f"{real_mon},{path}"],
            timeout=6, check=True,
        )

    elif backend == "swww":
        subprocess.run(
            ["swww", "img", path,
             "--transition-type", "grow",
             "--transition-fps", "60",
             "--transition-step", "40"],
            timeout=9, check=True,
        )

    elif backend == "swaybg":
        subprocess.run(["pkill", "-x", "swaybg"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        cmd = ["swaybg", "-i", path, "-m", "fill"]
        if mon:
            cmd = ["swaybg", "-o", mon, "-i", path, "-m", "fill"]
        subprocess.Popen(cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    elif backend == "wbg":
        subprocess.run(["pkill", "-x", "wbg"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        subprocess.Popen(["wbg", path], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    else:
        raise ValueError(f"Unknown backend: {backend}")


def backend_available(name: str) -> bool:
    if name == "hyprpaper":
        return bool(shutil.which("hyprctl"))
    return bool(shutil.which(name))


def available_backends() -> list[str]:
    return [b for b in ALL_BACKENDS if backend_available(b)]


def kill_other_backends(current: str):
    procs = {"hyprpaper": "hyprpaper", "swww": "swww", "swaybg": "swaybg", "wbg": "wbg"}
    for name, proc in procs.items():
        if name != current:
            subprocess.run(["pkill", "-f", proc],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)


# ──────────────────────────────────────────── Daemon ────────────

def is_daemon_running() -> bool:
    try:
        subprocess.check_output(["pgrep", "-f", DYNAMIC_DAEMON], timeout=2)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def start_daemon():
    if is_daemon_running():
        return
    try:
        subprocess.Popen(
            ["nohup", DYNAMIC_DAEMON],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"[waypaper-y] Failed to start {DYNAMIC_DAEMON}: {e}")


def stop_daemon():
    subprocess.run(["pkill", "-f", DYNAMIC_DAEMON],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)


# ──────────────────────────────────────────── Set wallpaper ─────

def _safe_markup(msg: str) -> str:
    return msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def set_wallpaper(path: str, cfg: "Config", app: "WallpaperApp") -> None:
    backend = cfg.backend
    now = time.monotonic()

    if backend == "hyprpaper":
        if hasattr(app, "last_static_change") and now - app.last_static_change < COOLDOWN_SECONDS:
            if app.status_label:
                app.status_label.set_markup(
                    f"<span foreground='#e66100'>Cooldown… ({COOLDOWN_SECONDS:.1f}s)</span>"
                )
            return
        app.last_static_change = now
        if app.cooldown_label:
            app.start_cooldown_countdown()

    try:
        kill_other_backends(backend)
        apply_wallpaper(backend, path, cfg.monitor)
        cfg.last_wallpaper = path
        if cfg.mode == "static":
            write_autostart(app.compositor, "static")

        if app.status_label:
            app.status_label.set_markup(
                f"<span foreground='#33d17a'>✓ {os.path.basename(path)}</span>"
            )
            GLib.timeout_add_seconds(4, lambda: app.status_label and app.status_label.set_markup("") or False)

    except subprocess.TimeoutExpired:
        if app.status_label:
            app.status_label.set_markup("<span foreground='#ed333b'>Command timed out</span>")
    except Exception as e:
        if app.status_label:
            app.status_label.set_markup(
                f"<span foreground='#ed333b'>Failed: {_safe_markup(str(e))}</span>"
            )


# ──────────────────────────────────────────── App ───────────────

class WallpaperApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.waypapery.WallpaperPicker")
        self.cfg = Config()
        self.compositor = detect_compositor()
        self.monitors: list[str] = list_monitors(self.compositor)

        self.last_static_change = 0.0
        self.cooldown_timeout_id = None
        self.thumb_count = 0

        self.win: Optional[Gtk.ApplicationWindow] = None
        self.spinner: Optional[Gtk.Spinner] = None
        self.grid: Optional[Gtk.Grid] = None
        self.cooldown_label: Optional[Gtk.Label] = None
        self.status_label: Optional[Gtk.Label] = None
        self.dynamic_status_label: Optional[Gtk.Label] = None
        self.daemon_missing_label: Optional[Gtk.Label] = None
        self.autostart_switch: Optional[Gtk.Switch] = None

        self.connect("activate", self.on_activate)

    # ── cooldown ────────────────────────────────────────────────

    def start_cooldown_countdown(self):
        if self.cooldown_timeout_id:
            GLib.source_remove(self.cooldown_timeout_id)
        self._update_cooldown(COOLDOWN_SECONDS)
        self.cooldown_timeout_id = GLib.timeout_add(180, self._tick_cooldown)

    def _tick_cooldown(self):
        remaining = max(0, self.last_static_change + COOLDOWN_SECONDS - time.monotonic())
        self._update_cooldown(remaining)
        if remaining <= 0.1:
            self.cooldown_timeout_id = None
            return False
        return True

    def _update_cooldown(self, remaining: float):
        if not self.cooldown_label:
            return
        if remaining > 0.2:
            self.cooldown_label.set_markup(f"<small>Cooldown • {remaining:.1f}s</small>")
            self.cooldown_label.set_visible(True)
        else:
            self.cooldown_label.set_markup("")
            self.cooldown_label.set_visible(False)

    # ── activate ────────────────────────────────────────────────

    def on_activate(self, _app):
        self.win = Gtk.ApplicationWindow(application=self, title="Waypaper-Y")
        self.win.set_default_size(1080, 820)
        self.win.set_decorated(False)

        self._apply_styles()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.win.set_child(main_box)

        header = self._build_header_bar()
        main_box.append(header)

        notebook = Gtk.Notebook()
        notebook.set_tab_pos(Gtk.PositionType.TOP)
        notebook.set_vexpand(True)
        main_box.append(notebook)

        notebook.append_page(self._build_static_page(),   Gtk.Label(label="  Static  "))
        notebook.append_page(self._build_dynamic_page(),  Gtk.Label(label="  Dynamic  "))
        notebook.append_page(self._build_settings_page(), Gtk.Label(label="  Settings  "))
        notebook.append_page(self._build_about_page(),    Gtk.Label(label="  About  "))

        # open on whichever mode was last active
        notebook.set_current_page(1 if self.cfg.mode == "dynamic" else 0)
        notebook.connect("notify::page", self.on_tab_changed)

        self.win.present()

        GLib.idle_add(self.load_wallpapers_async)
        GLib.idle_add(self.update_dynamic_status)

    # ── styles ───────────────────────────────────────────────────

    def _apply_styles(self):
        css = """
        window {
            font-family: system-ui, "Segoe UI", Cantarell, sans-serif;
            font-size: 1.05rem;
        }
        headerbar {
            border-bottom: 1px solid alpha(@borders, 0.6);
            background: alpha(@window_bg_color, 0.92);
        }
        button.thumbnail-btn {
            border-radius: 14px;
            padding: 6px;
            border: 1px solid transparent;
            transition: all 220ms cubic-bezier(0.4, 0, 0.2, 1);
            background: alpha(@card_bg_color, 0.4);
        }
        button.thumbnail-btn:hover {
            background: @accent_bg_color;
            border-color: @accent_fg_color;
            transform: scale(1.07);
        }
        button.thumbnail-btn:active { transform: scale(0.97); }
        picture.thumbnail-pic {
            border-radius: 10px;
            box-shadow: 0 3px 12px rgba(0,0,0,0.22);
        }
        label.status, label.dynamic-status {
            padding: 10px 22px;
            border-radius: 10px;
            background: alpha(@accent_bg_color, 0.13);
            margin: 8px 0;
        }
        label.warning  { color: #ed333b; font-weight: 500; }
        label.success  { color: #33d17a; font-weight: 500; }
        .dim-label     { opacity: 0.7; }
        .about-title   { font-size: 2.4rem; font-weight: bold; margin: 20px 0 8px; }
        .about-subtitle{ font-size: 1.4rem; margin-bottom: 16px; opacity: 0.85; }
        .settings-section { font-size: 1.05rem; font-weight: bold; margin-top: 12px; opacity: 0.75; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            self.win.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # ── header bar ───────────────────────────────────────────────

    def _build_header_bar(self) -> Gtk.HeaderBar:
        header = Gtk.HeaderBar()
        header.set_show_title_buttons(False)

        title = Gtk.Label(label="Waypaper-Y")
        title.add_css_class("heading")
        title.set_margin_start(12)
        header.set_title_widget(title)

        box = Gtk.Box(spacing=12)
        box.set_margin_end(12)

        comp_lbl = Gtk.Label(label=self.compositor)
        comp_lbl.add_css_class("dim-label")
        box.append(comp_lbl)

        header.pack_end(box)
        return header

    # ── static page ──────────────────────────────────────────────

    def _build_static_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.set_margin_top(16)
        page.set_margin_bottom(16)
        page.set_margin_start(24)
        page.set_margin_end(24)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(48, 48)
        self.spinner.start()
        self.spinner.set_halign(Gtk.Align.CENTER)
        page.append(self.spinner)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        page.append(scrolled)

        self.grid = Gtk.Grid()
        self.grid.set_row_spacing(20)
        self.grid.set_column_spacing(20)
        self.grid.set_halign(Gtk.Align.CENTER)
        scrolled.set_child(self.grid)

        self.cooldown_label = Gtk.Label()
        self.cooldown_label.add_css_class("dim-label")
        self.cooldown_label.set_halign(Gtk.Align.CENTER)
        self.cooldown_label.set_visible(False)
        page.append(self.cooldown_label)

        self.status_label = Gtk.Label()
        self.status_label.add_css_class("status")
        self.status_label.set_halign(Gtk.Align.CENTER)
        page.append(self.status_label)

        return page

    # ── dynamic page ─────────────────────────────────────────────

    def _build_dynamic_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        page.set_halign(Gtk.Align.CENTER)
        page.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("preferences-desktop-wallpaper-symbolic")
        icon.set_pixel_size(96)
        page.append(icon)

        title = Gtk.Label(label="<b><span size='xx-large'>Dynamic Mode</span></b>")
        title.set_use_markup(True)
        page.append(title)

        self.dynamic_status_label = Gtk.Label()
        self.dynamic_status_label.add_css_class("dynamic-status")
        page.append(self.dynamic_status_label)

        self.daemon_missing_label = Gtk.Label()
        self.daemon_missing_label.set_markup(
            f"<span foreground='#e66100'><b>wpydynamic</b> not found in PATH.\n"
            f"Install it to ~/.local/bin/ or /usr/local/bin/</span>"
        )
        self.daemon_missing_label.set_use_markup(True)
        self.daemon_missing_label.set_justify(Gtk.Justification.CENTER)
        self.daemon_missing_label.set_visible(False)
        page.append(self.daemon_missing_label)

        # start / stop / refresh
        btn_box = Gtk.Box(spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)

        start_btn = Gtk.Button(label="Start")
        start_btn.add_css_class("suggested-action")
        start_btn.connect("clicked", self._on_dynamic_start)
        btn_box.append(start_btn)

        stop_btn = Gtk.Button(label="Stop")
        stop_btn.add_css_class("destructive-action")
        stop_btn.connect("clicked", self._on_dynamic_stop)
        btn_box.append(stop_btn)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", lambda _: GLib.idle_add(self.update_dynamic_status))
        btn_box.append(refresh_btn)

        page.append(btn_box)

        # autostart on login toggle
        autostart_row = Gtk.Box(spacing=12)
        autostart_row.set_halign(Gtk.Align.CENTER)
        autostart_row.set_margin_top(8)

        autostart_row.append(Gtk.Label(label="Start on login"))

        self.autostart_switch = Gtk.Switch()
        self.autostart_switch.set_active(autostart_mode_active())
        self.autostart_switch.connect("notify::active", self._on_autostart_toggled)
        autostart_row.append(self.autostart_switch)

        page.append(autostart_row)

        return page

    def _on_dynamic_start(self, _):
        if not shutil.which(DYNAMIC_DAEMON):
            if self.daemon_missing_label:
                self.daemon_missing_label.set_visible(True)
            return
        if self.daemon_missing_label:
            self.daemon_missing_label.set_visible(False)

        stop_daemon()  # kill any stale instance first
        self.cfg.mode = "dynamic"
        write_autostart(self.compositor, "dynamic")
        start_daemon()
        GLib.timeout_add(900, self.update_dynamic_status)

    def _on_dynamic_stop(self, _):
        stop_daemon()
        self.cfg.mode = "static"
        # restore last wallpaper so static autostart is correct
        write_autostart(self.compositor, "static")
        GLib.idle_add(self.update_dynamic_status)

    def _on_autostart_toggled(self, switch: Gtk.Switch, _):
        if switch.get_active():
            mode = self.cfg.mode  # 'dynamic' or 'static'
            write_autostart(self.compositor, mode)
        else:
            write_autostart(self.compositor, "off")

    # ── settings page ────────────────────────────────────────────

    def _build_settings_page(self) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_halign(Gtk.Align.CENTER)
        outer.set_margin_top(32)
        outer.set_margin_bottom(32)
        outer.set_margin_start(32)
        outer.set_margin_end(32)

        form = Gtk.Grid()
        form.set_row_spacing(14)
        form.set_column_spacing(18)
        form.set_halign(Gtk.Align.CENTER)

        row = 0

        def section(label: str):
            nonlocal row
            lbl = Gtk.Label(label=label)
            lbl.add_css_class("settings-section")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_top(8)
            form.attach(lbl, 0, row, 2, 1)
            row += 1

        def field(label: str, widget: Gtk.Widget):
            nonlocal row
            lbl = Gtk.Label(label=label)
            lbl.set_halign(Gtk.Align.END)
            lbl.set_valign(Gtk.Align.CENTER)
            form.attach(lbl, 0, row, 1, 1)
            form.attach(widget, 1, row, 1, 1)
            row += 1

        # ── Wallpapers ───────────────────────────────
        section("Wallpapers")

        wp_box = Gtk.Box(spacing=8)
        self._wp_entry = Gtk.Entry()
        self._wp_entry.set_text(str(self.cfg.wallpaper_dir))
        self._wp_entry.set_width_chars(38)
        wp_box.append(self._wp_entry)

        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._on_browse_wallpaper_dir)
        wp_box.append(browse_btn)
        field("Wallpaper directory", wp_box)

        self._cols_spin = Gtk.SpinButton()
        self._cols_spin.set_range(1, 20)
        self._cols_spin.set_increments(1, 5)
        self._cols_spin.set_value(self.cfg.cols_per_row)
        self._cols_spin.set_width_chars(4)
        field("Wallpapers per row", self._cols_spin)

        # ── Display ──────────────────────────────────
        section("Display")

        mon_list = self.monitors if self.monitors else ["*"]
        if "*" not in mon_list:
            mon_list = ["*"] + mon_list

        mon_store = Gio.ListStore.new(Gtk.StringObject)
        for m in mon_list:
            mon_store.append(Gtk.StringObject.new(m))

        self._mon_dropdown = Gtk.DropDown()
        self._mon_dropdown.set_model(mon_store)
        self._mon_dropdown.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        cur_mon = self.cfg.monitor
        sel_idx = mon_list.index(cur_mon) if cur_mon in mon_list else 0
        self._mon_dropdown.set_selected(sel_idx)
        field("Monitor", self._mon_dropdown)

        # backend dropdown
        avail = available_backends()
        if not avail:
            avail = ALL_BACKENDS  # show all even if not installed

        be_store = Gio.ListStore.new(Gtk.StringObject)
        for b in avail:
            be_store.append(Gtk.StringObject.new(b))

        self._be_dropdown = Gtk.DropDown()
        self._be_dropdown.set_model(be_store)
        self._be_dropdown.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        cur_be = self.cfg.backend
        be_idx = avail.index(cur_be) if cur_be in avail else 0
        self._be_dropdown.set_selected(be_idx)
        field("Backend", self._be_dropdown)

        comp_val = Gtk.Label(label=self.compositor)
        comp_val.set_halign(Gtk.Align.START)
        comp_val.add_css_class("dim-label")
        field("Compositor (detected)", comp_val)

        # ── Thumbnail Cache ──────────────────────────
        section("Thumbnail Cache")

        self._cache_switch = Gtk.Switch()
        self._cache_switch.set_active(self.cfg.cache_enabled)
        self._cache_switch.set_halign(Gtk.Align.START)
        self._cache_switch.connect("notify::active", self._on_cache_toggled)
        field("Enable thumbnail cache", self._cache_switch)

        clear_cache_btn = Gtk.Button(label="Clear cache")
        clear_cache_btn.add_css_class("destructive-action")
        clear_cache_btn.set_halign(Gtk.Align.START)
        clear_cache_btn.connect("clicked", self._on_clear_cache)
        field("Cached thumbnails", clear_cache_btn)

        # ── Dynamic mode ─────────────────────────────
        section("Dynamic Mode")

        self._min_spin = Gtk.SpinButton()
        self._min_spin.set_range(30, 7200)
        self._min_spin.set_increments(30, 60)
        self._min_spin.set_value(self.cfg.min_interval)
        self._min_spin.set_width_chars(6)
        field("Min interval (seconds)", self._min_spin)

        self._max_spin = Gtk.SpinButton()
        self._max_spin.set_range(60, 86400)
        self._max_spin.set_increments(30, 60)
        self._max_spin.set_value(self.cfg.max_interval)
        self._max_spin.set_width_chars(6)
        field("Max interval (seconds)", self._max_spin)

        # ── Save ─────────────────────────────────────
        row += 1  # spacer

        self._settings_status = Gtk.Label()
        self._settings_status.set_halign(Gtk.Align.CENTER)
        form.attach(self._settings_status, 0, row, 2, 1)
        row += 1

        save_btn = Gtk.Button(label="Save settings")
        save_btn.add_css_class("suggested-action")
        save_btn.set_halign(Gtk.Align.CENTER)
        save_btn.connect("clicked", self._on_save_settings)
        form.attach(save_btn, 0, row, 2, 1)

        outer.append(form)
        scroll.set_child(outer)
        return scroll

    def _on_cache_toggled(self, switch: Gtk.Switch, _):
        enabled = switch.get_active()
        self.cfg.cache_enabled = enabled
        if not enabled:
            # Ask whether to clear existing cache
            dialog = Gtk.MessageDialog(
                transient_for=self.win,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="Clear existing cache?",
            )
            lbl = Gtk.Label(label="Thumbnail caching has been disabled.\nWould you like to delete the existing cached thumbnails?")
            lbl.set_wrap(True)
            lbl.set_margin_start(16)
            lbl.set_margin_end(16)
            lbl.set_margin_bottom(12)
            dialog.get_message_area().append(lbl)
            dialog.connect("response", self._on_clear_cache_response)
            dialog.present()

    def _on_clear_cache_response(self, dialog: Gtk.MessageDialog, response_id: int):
        dialog.destroy()
        if response_id == Gtk.ResponseType.YES:
            self._do_clear_cache()

    def _on_clear_cache(self, _):
        dialog = Gtk.MessageDialog(
            transient_for=self.win,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Clear thumbnail cache?",
        )
        lbl = Gtk.Label(label="This will delete all cached thumbnails.\nThey will be regenerated next time you open the app.")
        lbl.set_wrap(True)
        lbl.set_margin_start(16)
        lbl.set_margin_end(16)
        lbl.set_margin_bottom(12)
        dialog.get_message_area().append(lbl)
        dialog.connect("response", self._on_clear_cache_response)
        dialog.present()

    def _do_clear_cache(self):
        count = clear_thumbnail_cache()
        if self._settings_status:
            self._settings_status.set_markup(
                f"<span foreground='#33d17a'>Cleared {count} cached thumbnail(s)</span>"
            )
            GLib.timeout_add_seconds(3, lambda: self._settings_status.set_markup("") or False)

    def _on_browse_wallpaper_dir(self, _):
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose wallpaper directory")
        dialog.select_folder(self.win, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                self._wp_entry.set_text(folder.get_path())
        except Exception:
            pass

    def _on_save_settings(self, _):
        errors = []

        # monitor
        mon_idx = self._mon_dropdown.get_selected()
        mon_item = self._mon_dropdown.get_model().get_item(mon_idx)
        if mon_item:
            self.cfg.monitor = mon_item.get_string()

        # backend
        be_idx = self._be_dropdown.get_selected()
        be_item = self._be_dropdown.get_model().get_item(be_idx)
        if be_item:
            self.cfg.backend = be_item.get_string()

        # wallpaper dir
        wp_dir = self._wp_entry.get_text().strip()
        if wp_dir:
            self.cfg.set("wallpaper_dir", wp_dir)

        # cols per row
        self.cfg.cols_per_row = int(self._cols_spin.get_value())

        # intervals
        min_val = int(self._min_spin.get_value())
        max_val = int(self._max_spin.get_value())
        if min_val >= max_val:
            errors.append("Min interval must be less than max")
        else:
            self.cfg.set("min_interval", str(min_val))
            self.cfg.set("max_interval", str(max_val))

        if errors:
            self._settings_status.set_markup(
                f"<span foreground='#ed333b'>{errors[0]}</span>"
            )
        else:
            self._settings_status.set_markup("<span foreground='#33d17a'>Settings saved!</span>")
            GLib.timeout_add_seconds(3, lambda: self._settings_status.set_markup("") or False)

            # if daemon is running, restart it so it picks up new config
            if is_daemon_running():
                stop_daemon()
                GLib.timeout_add(500, lambda: start_daemon() or False)

    # ── about page ───────────────────────────────────────────────

    def _build_about_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        page.set_halign(Gtk.Align.CENTER)
        page.set_valign(Gtk.Align.CENTER)
        page.set_margin_top(60)
        page.set_margin_bottom(60)

        title = Gtk.Label(label="Waypaper-Y")
        title.add_css_class("about-title")
        page.append(title)

        by = Gtk.Label(label="by Yurz • v1.4")
        by.add_css_class("about-subtitle")
        page.append(by)

        desc = Gtk.Label()
        desc.set_markup(
            "Static &amp; dynamic wallpaper switching for Wayland\n\n"
            "• Supports <b>Hyprland</b>, <b>Sway</b>, <b>Niri</b> and more\n"
            "• Backends: <b>hyprpaper</b>, <b>swww</b>, <b>swaybg</b>, <b>wbg</b>\n"
            "• Config at <tt>~/.config/waypapery/config.ini</tt>\n"
            "• Dynamic daemon: <tt>wpydynamic</tt>\n\n"
            "Enjoy your wallpapers~ ✨"
        )
        desc.set_use_markup(True)
        desc.set_wrap(True)
        desc.set_wrap_mode(Gtk.WrapMode.WORD)
        desc.set_justify(Gtk.Justification.CENTER)
        desc.set_max_width_chars(60)
        page.append(desc)

        return page

    # ── tab changed ──────────────────────────────────────────────

    def on_tab_changed(self, notebook: Gtk.Notebook, _):
        page = notebook.get_current_page()
        if page == 1:
            GLib.idle_add(self.update_dynamic_status)

    def update_dynamic_status(self):
        if not self.dynamic_status_label:
            return False

        daemon_exists = bool(shutil.which(DYNAMIC_DAEMON))
        running = is_daemon_running() if daemon_exists else False

        if not daemon_exists:
            msg = "<span foreground='#e66100'><b>wpydynamic</b> not found in PATH</span>"
        elif running:
            msg = "<span size='large' weight='bold' foreground='#33d17a'>● Dynamic mode active</span>"
        else:
            msg = "<span size='large' foreground='#ed333b'>○ Dynamic mode not running</span>"

        self.dynamic_status_label.set_markup(msg)

        if self.autostart_switch:
            self.autostart_switch.set_active(autostart_mode_active())

        return False

    # ── wallpaper loading ─────────────────────────────────────────

    def load_wallpapers_async(self):
        wp_dir = self.cfg.wallpaper_dir
        try:
            files = sorted(
                p for p in wp_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            )
        except OSError:
            files = []

        if not files:
            self.spinner.set_visible(False)
            self.spinner.stop()
            if self.status_label:
                self.status_label.set_markup(
                    f"<span foreground='#e66100'>No wallpapers found in {wp_dir}</span>"
                )
            return False

        ctx = GLib.MainContext.default()

        for path in files:
            try:
                if self.cfg.cache_enabled:
                    png_bytes = load_thumbnail_cached(path)
                else:
                    png_bytes = load_thumbnail_uncached(path)

                texture = Gdk.Texture.new_from_bytes(GLib.Bytes(png_bytes))

                pic = Gtk.Picture.new_for_paintable(texture)
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic.set_size_request(THUMB_SIZE, int(THUMB_SIZE * 0.65))
                pic.add_css_class("thumbnail-pic")

                btn = Gtk.Button()
                btn.set_child(pic)
                btn.set_tooltip_text(path.name)
                btn.add_css_class("thumbnail-btn")
                btn.connect("clicked", lambda _, p=str(path): set_wallpaper(p, self.cfg, self))

                col = self.thumb_count % self.cfg.cols_per_row
                row_idx = self.thumb_count // self.cfg.cols_per_row
                self.grid.attach(btn, col, row_idx, 1, 1)
                self.thumb_count += 1

                while ctx.pending():
                    ctx.iteration(False)

            except Exception as e:
                print(f"[waypaper-y] Failed to load {path.name}: {e}")

        self.spinner.set_visible(False)
        self.spinner.stop()
        return False


# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = WallpaperApp()
    app.run()
