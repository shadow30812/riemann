"""
How LibreOffice can be installed on this computer, as commands to show the
person and run only when they click Install (ui/office_install.py).

install_plans() lists the ways that apply, best first:

- Windows: winget, which runs LibreOffice's own installer window
  (--interactive), where the person accepts its licence and Windows asks for
  permission.
- Linux: the distribution's package manager, found from /etc/os-release (ID,
  then ID_LIKE), through pkexec, which shows the system's password dialog:
  dnf, apt-get, zypper or pacman. Then Flatpak from Flathub for this user,
  which needs no password but downloads more; it adds the Flathub remote for
  the user first, since a computer may have it only system-wide. Some
  distributions ship no LibreOffice (RHEL 10 and its rebuilds), so the dialog
  offers the next plan when one fails.
- Elsewhere, or when nothing applies: the download page
  (endpoints.LIBREOFFICE_DOWNLOAD), opened in the browser.

AGENTS.md makes an exception for these commands to the rule that downloads
are pinned by version and SHA-256: the package managers check their own
downloads (winget against its manifest's hash, distributions and Flathub by
signature), but no version is pinned. It covers LibreOffice only, and only
on the person's click.

No Qt imports.
"""

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from .endpoints import FLATHUB_REPO
from .office import FLATPAK_ID

PACKAGES = ("libreoffice-writer", "libreoffice-calc", "libreoffice-impress")
FAMILIES = (
    (("fedora", "rhel", "centos", "rocky", "almalinux", "ol"), "dnf", ["dnf", "install", "-y", *PACKAGES]),
    (
        ("debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin"),
        "apt-get",
        ["apt-get", "install", "-y", *PACKAGES],
    ),
    (
        ("opensuse", "suse", "sles", "opensuse-leap", "opensuse-tumbleweed"),
        "zypper",
        ["zypper", "--non-interactive", "install", *PACKAGES],
    ),
    (("arch", "manjaro", "endeavouros"), "pacman", ["pacman", "-S", "--noconfirm", "libreoffice-fresh"]),
)
WINGET = ["winget", "install", "--exact", "--id", "TheDocumentFoundation.LibreOffice", "--source", "winget",
          "--interactive", "--accept-source-agreements"]


@dataclass
class InstallPlan:
    name: str
    note: str
    steps: list[list[str]] = field(default_factory=list)

    def shown(self) -> str:
        return "\n".join(" ".join(step) for step in self.steps)


def os_release(path: str = "/etc/os-release") -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key:
                    values[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def system_plan(release: dict[str, str], which: Callable[[str], str | None] = shutil.which) -> InstallPlan | None:
    ids = [release.get("ID", "").lower(), *release.get("ID_LIKE", "").lower().split()]
    if not which("pkexec"):
        return None
    for names, tool, command in FAMILIES:
        if any(i in names for i in ids) and which(tool):
            distro = release.get("NAME") or release.get("ID") or "Linux"
            return InstallPlan(
                f"{distro} packages",
                "Installs Writer, Calc and Impress with the system's package manager. It asks for your password.",
                [["pkexec", *command]],
            )
    return None


def flatpak_plan(which: Callable[[str], str | None] = shutil.which) -> InstallPlan | None:
    if not which("flatpak"):
        return None
    return InstallPlan(
        "Flatpak from Flathub",
        "Installs LibreOffice for you only, with no password. The download is larger, since it brings its own "
        "libraries.",
        [
            ["flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO],
            ["flatpak", "install", "--user", "-y", "--noninteractive", "flathub", FLATPAK_ID],
        ],
    )


def install_plans(
    platform: str = sys.platform,
    release: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[InstallPlan]:
    if platform == "win32":
        if which("winget"):
            return [InstallPlan(
                "winget",
                "Opens LibreOffice's installer, where you accept its licence. Windows asks for permission.",
                [list(WINGET)],
            )]
        return []
    if platform.startswith("linux"):
        plans = [system_plan(os_release() if release is None else release, which), flatpak_plan(which)]
        return [p for p in plans if p is not None]
    return []


def install_env() -> dict[str, str]:
    """The environment for the installers: Riemann's own library paths left out, as for LibreOffice."""
    from .office import STRIPPED_ENV

    return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
