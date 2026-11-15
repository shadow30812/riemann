"""
Downloads the PDFium library into libs/ and checks it against pinned SHA-256s.

The builds come from bblanchon/pdfium-binaries on GitHub. Each platform is
pinned to the release Riemann was tested with (Linux chromium/7643, Windows
chromium/7749), by the hash of the release archive and of the library inside
it. A library already in libs/ with the pinned hash is left alone; one with any
other hash is replaced. To move to a newer PDFium, change PINS to the new tag
and both hashes (the archive's is shown on the release page), then run the
reader tests.

Usage: python scripts/fetch_pdfium.py [linux-x64|win-x64|all]. Without an
argument it fetches the build for this machine. Standard library only, so it
runs before any environment is set up.
"""

import hashlib
import io
import os
import sys
import tarfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBS = os.path.join(ROOT, "libs")
URL = "https://github.com/bblanchon/pdfium-binaries/releases/download/chromium/{tag}/pdfium-{platform}.tgz"

PINS = {
    "linux-x64": {
        "tag": "7643",
        "member": "lib/libpdfium.so",
        "file": "libpdfium.so",
        "archive_sha256": "d407cebfc4a3ad5840d1154d8b384a555a63d8c499e67cc44746f6e8511a9d87",
        "file_sha256": "863c0d9bff66342da93b974bcdb5f873d5894fd815e938401c8fd229f5b31c3f",
    },
    "win-x64": {
        "tag": "7749",
        "member": "bin/pdfium.dll",
        "file": "pdfium.dll",
        "archive_sha256": "a0f7ca96b3a1b2f509bb003f717147bc612e8a3f9b435cd8d1adb97c70cb2dbd",
        "file_sha256": "7167aee6bb3d2724ee62fd83bbeb8883edc786a6e1999782857d4952536a0ed3",
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def this_platform() -> str:
    if sys.platform == "win32":
        return "win-x64"
    if sys.platform.startswith("linux"):
        return "linux-x64"
    raise SystemExit(f"No pinned PDFium build for {sys.platform}; pass linux-x64 or win-x64.")


def fetch(platform: str) -> None:
    pin = PINS[platform]
    dest = os.path.join(LIBS, pin["file"])
    if os.path.exists(dest):
        with open(dest, "rb") as f:
            if sha256(f.read()) == pin["file_sha256"]:
                print(f"ok {pin['file']} (chromium/{pin['tag']})")
                return
    url = URL.format(tag=pin["tag"], platform=platform)
    print(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as resp:
        archive = resp.read()
    if sha256(archive) != pin["archive_sha256"]:
        raise SystemExit(f"HASH MISMATCH {url}: expected {pin['archive_sha256']}, got {sha256(archive)}")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.extractfile(pin["member"])
        if member is None:
            raise SystemExit(f"{pin['member']} missing from {url}")
        data = member.read()
    if sha256(data) != pin["file_sha256"]:
        raise SystemExit(f"HASH MISMATCH {pin['member']}: expected {pin['file_sha256']}, got {sha256(data)}")
    os.makedirs(LIBS, exist_ok=True)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    print(f"ok {pin['file']} (chromium/{pin['tag']})")


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else this_platform()
    platforms = list(PINS) if arg == "all" else [arg]
    for platform in platforms:
        if platform not in PINS:
            raise SystemExit(f"Unknown platform {platform}; choose from {', '.join(PINS)} or all.")
        fetch(platform)


if __name__ == "__main__":
    main()
