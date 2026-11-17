"""
Linux desktop integration: the .desktop entry, icon and MIME associations.

Notes:

install_linux_integration: Running from source would point the menu entry at a
.py file, so only packaged (PyInstaller/Nuitka) builds register themselves.
Already registered (the usual case): skip the rewrite and the update-desktop-
database run, which happened on every launch.

MIME_TYPES: text/plain covers source files and logs too, since their own types
(text/x-python, text/x-log...) derive from it. IMAGE_MIME_TYPES are the
common picture types; registering them adds Riemann to "Open with" without
making it the default. scripts/install_icon.sh writes the same list.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

from ..core.assets import icon_path, is_frozen

IMAGE_MIME_TYPES = (
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/tiff", "image/bmp",
    "image/svg+xml", "image/vnd.microsoft.icon", "image/vnd.adobe.photoshop", "image/jp2",
    "image/x-portable-anymap", "image/x-tga", "image/x-pcx", "image/apng"
)
OFFICE_MIME_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel",
    "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.spreadsheet", "application/vnd.oasis.opendocument.graphics",
    "application/rtf", "text/rtf"
)
MIME_TYPES = (
    "application/pdf", "text/markdown", "text/plain", "text/html", "inode/directory", "application/epub+zip",
    "text/csv", "text/tab-separated-values"
) + OFFICE_MIME_TYPES + IMAGE_MIME_TYPES


def install_linux_integration():
    """
    Detects if running as a Linux executable and installs/updates
    the .desktop shortcut and icon in the user's local share.
    """
    if not sys.platform.startswith("linux"):
        return

    is_packaged = is_frozen() or bool(os.environ.get("NUITKA_ONEFILE_BINARY"))
    if not is_packaged:
        return

    try:
        app_name = "Riemann"
        home = Path.home()
        apps_dir = home / ".local" / "share" / "applications"
        icons_dir = home / ".local" / "share" / "icons"

        apps_dir.mkdir(parents=True, exist_ok=True)
        icons_dir.mkdir(parents=True, exist_ok=True)

        internal_icon_path = icon_path("Icon.png")

        if not os.path.exists(internal_icon_path):
            print(f"Warning: Could not find internal icon at {internal_icon_path}")
            return

        persistent_icon_path = icons_dir / "riemann.png"
        try:
            icon_current = (
                persistent_icon_path.read_bytes()
                == Path(internal_icon_path).read_bytes()
            )
        except OSError:
            icon_current = False
        if not icon_current:
            shutil.copy2(internal_icon_path, persistent_icon_path)

        desktop_file_path = apps_dir / f"{app_name}.desktop"
        exe_path = os.path.abspath(sys.argv[0])

        desktop_entry = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={app_name}\n"
            "GenericName=Document Reader\n"
            "Comment=Read PDFs, Office files, e-books and more, and browse the web\n"
            f'Exec="{exe_path}" %F\n'
            f"Icon={persistent_icon_path}\n"
            "Terminal=false\n"
            "Categories=Office;Viewer;Utility;\n"
            f"StartupWMClass={app_name}\n"
            f"MimeType={';'.join(MIME_TYPES)};\n"
            "Actions=NewWindow;\n\n"
            "[Desktop Action NewWindow]\n"
            "Name=New Window\n"
            f'Exec="{exe_path}" --new-window\n'
        )

        try:
            if desktop_file_path.read_text(encoding="utf-8") == desktop_entry:
                return
        except OSError:
            pass

        with open(desktop_file_path, "w", encoding="utf-8") as f:
            f.write(desktop_entry)

        os.chmod(desktop_file_path, 0o755)
        try:
            subprocess.run(
                ["update-desktop-database", str(apps_dir)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        print(f"[Riemann] Integrated to desktop menu: {desktop_file_path}")

    except Exception as e:
        print(f"Icon integration warning: {e}")
