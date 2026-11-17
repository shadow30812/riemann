"""
LibreOffice: finding it, converting Office files with it, and the cache of
what it converted.

find_office() looks, in order, at: the path chosen in Settings
(OFFICE_PATH_KEY); soffice, libreoffice and versioned names such as
libreoffice26.2 on the PATH; /opt/libreoffice*/program/soffice, newest first;
the distribution folders under /usr/lib*; macOS's application folder; on
Windows the install path LibreOffice writes to the registry, then Program
Files; the Flatpak org.libreoffice.LibreOffice; the Snap. The answer is kept
for the session; refresh() forgets it, for "Check again" and after an
install. RIEMANN_NO_OFFICE=1 in the environment hides LibreOffice, for tests
and for trying the flows without it.

OfficeConversion(path, target) runs soffice --headless --convert-to target
into a folder of its own and returns the converted file, moved into the
cache. Only one conversion runs at a time (_RUN_LOCK): LibreOffice can't share
a profile between two processes. run() blocks, so callers use a worker
thread; cancel() from another thread kills the process, and so does the
TIMEOUT. The soffice Riemann starts is a launcher that starts soffice.bin
(and a Flatpak runs it in a sandbox), so the conversion runs in a process
group of its own and the whole group is killed (taskkill /T on Windows):
killing only the launcher would leave soffice.bin holding the profile, and
the next conversion would hand its work to it and find nothing. The user's
file is only read.

Riemann's own LibreOffice profile (profile_dir) means conversion works while
the person has LibreOffice open, and lets Riemann lock it down. Before every
conversion its registrymodifications.xcu is written afresh with
PROFILE_SETTINGS: macros never run, links to pictures and other files outside
the document are blocked (a default LibreOffice fetches a .docx's or .odt's
remote pictures while converting, which tells the sender the file was
opened), and no update check runs. Writing the file afresh also drops the
list of recent documents LibreOffice keeps there.

The child gets an environment without the Python, Qt and library search
paths of Riemann's own process (a packaged build sets LD_LIBRARY_PATH to its
bundle, which would break LibreOffice), with SAL_USE_VCLPLUGIN=svp so it never
opens a window, and NO_WINDOW on Windows.

open_in_office(path) opens the file in LibreOffice's own editor, with the
person's own profile and settings, detached from Riemann, for "Edit in
LibreOffice".

Converted files are cached under the cache folder, named by a hash of the
file's real path, size and modification time, the target format and the
LibreOffice found, so a changed file or another LibreOffice converts again.
prune() trims the cache to CACHE_LIMIT bytes, oldest first, after each
conversion. A caller may pass its own cache folder (an incognito tab passes
a temporary one it removes). clear_cache() deletes every converted copy and
the profile, for Settings > Privacy.
"""

import glob
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import QStandardPaths

from .processes import NO_WINDOW

TIMEOUT = 120.0
CACHE_LIMIT = 500 * 1024 * 1024
FOLDER = "office"
FLATPAK_ID = "org.libreoffice.LibreOffice"
VERSIONED = re.compile(r"^libreoffice\d+(\.\d+)*$")
STRIPPED_ENV = ("PYTHONPATH", "PYTHONHOME", "LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH")
PROFILE_SETTINGS = (
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    ("/org.openoffice.Office.Jobs/Jobs/org.openoffice.Office.Jobs:Job['UpdateCheck']/Arguments", "AutoCheckEnabled", "false"),
)

_RUN_LOCK = threading.Lock()
_found: Optional["Office"] = None
_searched = False


class OfficeError(Exception):
    pass


@dataclass(frozen=True)
class Office:
    command: tuple
    label: str
    identity: str

    @property
    def flatpak(self) -> bool:
        return self.command[:1] == ("flatpak",)


def _binary(path: str) -> Optional[Office]:
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return None
    real = os.path.realpath(path)
    try:
        stamp = os.stat(real).st_mtime_ns
    except OSError:
        stamp = 0
    return Office((real,), real, f"{real}:{stamp}")


def _on_path() -> List[str]:
    names = ["soffice", "libreoffice"]
    found = [shutil.which(n) for n in names]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        try:
            entries = sorted(os.listdir(folder), reverse=True)
        except OSError:
            continue
        found += [os.path.join(folder, e) for e in entries if VERSIONED.match(e)]
    return [p for p in found if p]


def _version_key(path: str):
    return [int(n) for n in re.findall(r"\d+", path)]


def _linux_folders() -> List[str]:
    opt = sorted(glob.glob("/opt/libreoffice*/program/soffice"), key=_version_key, reverse=True)
    lib = glob.glob("/usr/lib*/libreoffice/program/soffice") + glob.glob("/usr/local/lib*/libreoffice/program/soffice")
    return opt + lib


def _windows_folders() -> List[str]:
    paths = []
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in (r"SOFTWARE\LibreOffice\UNO\InstallPath", r"SOFTWARE\WOW6432Node\LibreOffice\UNO\InstallPath"):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        folder, _ = winreg.QueryValueEx(handle, "")
                    paths.append(os.path.join(folder, "soffice.exe"))
                except OSError:
                    pass
    except ImportError:
        pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            paths.append(os.path.join(base, "LibreOffice", "program", "soffice.exe"))
    return paths


def _flatpak() -> Optional[Office]:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return None
    try:
        result = subprocess.run(
            [flatpak, "info", "--show-commit", FLATPAK_ID],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, **NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return Office(("flatpak", "run", "--command=soffice", FLATPAK_ID), f"Flatpak {FLATPAK_ID}", f"flatpak:{result.stdout.strip()}")


def candidates() -> List[str]:
    if sys.platform == "win32":
        return _on_path() + _windows_folders()
    if sys.platform == "darwin":
        return _on_path() + ["/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    return _on_path() + _linux_folders() + ["/snap/bin/libreoffice"]


def find_office(override: str = "") -> Optional[Office]:
    """The LibreOffice to convert with, or None; override is the path chosen in Settings."""
    global _found, _searched
    if os.environ.get("RIEMANN_NO_OFFICE") == "1":
        return None
    if override:
        chosen = _binary(override)
        if chosen is not None:
            return chosen
    if not _searched:
        _found = next((o for o in map(_binary, candidates()) if o is not None), None)
        if _found is None and sys.platform.startswith("linux"):
            _found = _flatpak()
        _searched = True
    return _found


def refresh() -> None:
    global _found, _searched
    _found, _searched = None, False


def cache_root() -> str:
    return os.path.join(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation), FOLDER)


def profile_dir() -> str:
    return os.path.join(cache_root(), "profile")


def files_dir() -> str:
    return os.path.join(cache_root(), "files")


def _write_profile(folder: str) -> None:
    user = os.path.join(folder, "user")
    os.makedirs(user, exist_ok=True)
    items = "".join(
        f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse"><value>{value}</value></prop></item>\n'
        for path, name, value in PROFILE_SETTINGS
    )
    with open(os.path.join(user, "registrymodifications.xcu"), "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
            f"{items}</oor:items>\n"
        )


def _profile_url(folder: str) -> str:
    path = os.path.abspath(folder).replace("\\", "/")
    from urllib.parse import quote

    return "file://" + ("/" if not path.startswith("/") else "") + quote(path)


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    env["SAL_USE_VCLPLUGIN"] = "svp"
    return env


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **NO_WINDOW,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    proc.kill()


def open_in_office(path: str, office: Office) -> None:
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    detach = {"start_new_session": True} if os.name != "nt" else {}
    subprocess.Popen(
        list(office.command) + [os.path.abspath(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, **NO_WINDOW, **detach,
    )


def cache_key(path: str, target: str, office: Office) -> str:
    real = os.path.realpath(path)
    info = os.stat(real)
    text = f"{real}\0{info.st_size}\0{info.st_mtime_ns}\0{target}\0{office.identity}"
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def cached(path: str, target: str, office: Office, folder: Optional[str] = None) -> Optional[str]:
    try:
        name = os.path.join(folder or files_dir(), f"{cache_key(path, target, office)}.{target}")
    except OSError:
        return None
    if os.path.isfile(name):
        try:
            os.utime(name)
        except OSError:
            pass
        return name
    return None


def prune(folder: Optional[str] = None, limit: int = CACHE_LIMIT) -> None:
    folder = folder or files_dir()
    try:
        entries = [os.path.join(folder, e) for e in os.listdir(folder)]
    except OSError:
        return
    files = []
    for entry in entries:
        try:
            info = os.stat(entry)
        except OSError:
            continue
        if os.path.isfile(entry):
            files.append((info.st_mtime, info.st_size, entry))
    total = sum(size for _, size, _ in files)
    for _, size, entry in sorted(files):
        if total <= limit:
            break
        try:
            os.remove(entry)
            total -= size
        except OSError:
            pass


def clear_cache() -> None:
    shutil.rmtree(cache_root(), ignore_errors=True)


class OfficeConversion:
    """One conversion of path into target ("pdf", "xlsx"); run() returns the converted file."""

    def __init__(self, path: str, target: str = "pdf", office: Optional[Office] = None,
                 folder: Optional[str] = None, timeout: float = TIMEOUT):
        self.path, self.target, self.timeout = os.path.abspath(path), target, timeout
        self.office = office
        self.folder = folder
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._cancelled = False

    def command(self, outdir: str) -> list:
        return list(self.office.command) + [
            f"-env:UserInstallation={_profile_url(profile_dir())}",
            "--headless", "--invisible", "--norestore", "--nologo", "--nodefault", "--nolockcheck",
            "--convert-to", self.target, "--outdir", outdir, self.path,
        ]

    def run(self) -> str:
        name = os.path.basename(self.path)
        if self.office is None:
            raise OfficeError(f"Riemann needs LibreOffice to show {name}, and it isn't installed.")
        if not os.path.isfile(self.path):
            raise OfficeError(f"{name} is no longer there.")
        folder = self.folder or files_dir()
        hit = cached(self.path, self.target, self.office, folder)
        if hit:
            return hit
        os.makedirs(folder, exist_ok=True)
        with _RUN_LOCK:
            if self._cancelled:
                raise OfficeError("Cancelled.")
            outdir = tempfile.mkdtemp(prefix="convert-", dir=folder)
            try:
                return self._convert(outdir, folder, name)
            finally:
                shutil.rmtree(outdir, ignore_errors=True)

    def _convert(self, outdir: str, folder: str, name: str) -> str:
        _write_profile(profile_dir())
        with self._lock:
            if self._cancelled:
                raise OfficeError("Cancelled.")
            self._proc = subprocess.Popen(
                self.command(outdir),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=_child_env(), **NO_WINDOW, **({"start_new_session": True} if os.name != "nt" else {}),
            )
        try:
            self._proc.wait(self.timeout)
        except subprocess.TimeoutExpired:
            _kill(self._proc)
            self._proc.wait()
            raise OfficeError(f"LibreOffice took more than {int(self.timeout)} seconds to convert {name}, so Riemann stopped it.")
        if self._cancelled:
            raise OfficeError("Cancelled.")
        produced = [e for e in os.listdir(outdir) if e.lower().endswith("." + self.target)]
        if not produced:
            raise OfficeError(
                f"LibreOffice couldn't convert {name}. The file may be damaged or protected by a password."
            )
        final = os.path.join(folder, f"{cache_key(self.path, self.target, self.office)}.{self.target}")
        os.replace(os.path.join(outdir, produced[0]), final)
        prune(folder)
        return final

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._proc is not None:
                _kill(self._proc)
