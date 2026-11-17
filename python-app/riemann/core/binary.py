"""
Files Riemann has no viewer for, shown as bytes and never run.

core.doctypes calls a file "binary" when it is none of the kinds Riemann
reads. Such a file opens in ui/viewers/hex_tab.py, which lists its bytes:
the offset, sixteen bytes in hex, and the same bytes as text (printable
ASCII, a dot for the rest). Showing bytes is safe whatever they are. A file
does harm only when it is run, handed to another program, or parsed by a
decoder with bugs, and the hex view does none of these: it reads the file
with plain reads, a block at a time, and no library looks inside.

program_kind(path) names the kind of program a file is, or returns "":

- a file that starts with a program format's signature (PROGRAM_SIGNATURES):
  Linux and other Unix programs (ELF), Windows programs and libraries (MZ),
  Mac programs (Mach-O, any byte order, and universal binaries, whose
  CAFEBABE header Java class files share), WebAssembly, Windows shortcuts,
  and scripts that start with #!;
- a name in PROGRAM_SUFFIXES: Windows programs, installers, scripts and
  shortcuts that run when opened, and packages and disk images that install
  or start programs.

The permission bit isn't used: Windows file systems mounted on Linux mark
every file executable. A program gets a warning banner in the hex view,
and populate_open_with_menu (ui/components.py) offers no way to open it
outside Riemann, so Riemann never becomes a way to start one.

read_block(path, offset, size) reads up to size bytes at offset, for the
hex view's model, which keeps a few blocks cached.
"""

import os

ROW_BYTES = 16
BLOCK_BYTES = 64 * 1024
PROGRAM_SIGNATURES = (
    (b"\x7fELF", "Linux program"),
    (b"MZ", "Windows program"),
    (b"\xfe\xed\xfa\xce", "Mac program"),
    (b"\xfe\xed\xfa\xcf", "Mac program"),
    (b"\xce\xfa\xed\xfe", "Mac program"),
    (b"\xcf\xfa\xed\xfe", "Mac program"),
    (b"\xca\xfe\xba\xbe", "Mac or Java program"),
    (b"\x00asm", "WebAssembly program"),
    (b"L\x00\x00\x00\x01\x14\x02\x00", "Windows shortcut"),
    (b"#!", "script"),
)
PROGRAM_SUFFIXES = {
    ".exe": "Windows program",
    ".dll": "Windows library",
    ".sys": "Windows driver",
    ".scr": "Windows screen saver",
    ".com": "DOS program",
    ".cpl": "Windows control panel item",
    ".msi": "Windows installer",
    ".msix": "Windows installer",
    ".msp": "Windows installer",
    ".appx": "Windows installer",
    ".bat": "Windows script",
    ".cmd": "Windows script",
    ".ps1": "PowerShell script",
    ".vbs": "Windows script",
    ".vbe": "Windows script",
    ".jse": "Windows script",
    ".wsf": "Windows script",
    ".hta": "Windows web application",
    ".lnk": "Windows shortcut",
    ".pif": "Windows shortcut",
    ".jar": "Java program",
    ".apk": "Android app",
    ".appimage": "Linux program",
    ".run": "Linux installer",
    ".deb": "Linux package",
    ".rpm": "Linux package",
    ".flatpak": "Linux package",
    ".snap": "Linux package",
    ".so": "Linux library",
    ".dylib": "Mac library",
    ".dmg": "Mac disk image",
    ".pkg": "Mac installer",
    ".command": "Mac script",
}


def program_kind(path: str) -> str:
    lower = os.path.basename(path).lower()
    suffix = os.path.splitext(lower)[1]
    if suffix in PROGRAM_SUFFIXES:
        return PROGRAM_SUFFIXES[suffix]
    if ".so." in lower:
        return PROGRAM_SUFFIXES[".so"]
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return ""
    return next((kind for magic, kind in PROGRAM_SIGNATURES if head.startswith(magic)), "")


def read_block(path: str, offset: int, size: int = BLOCK_BYTES) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


def hex_row(data: bytes) -> str:
    """Sixteen bytes as hex pairs, with a wider gap after the eighth."""
    pairs = [f"{b:02X}" for b in data]
    return (" ".join(pairs[:8]) + "  " + " ".join(pairs[8:])).rstrip()


def text_row(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)
