"""Shared helpers for the benchmark scripts: corpus paths, machine info, stats, output."""

import json
import os
import platform
import statistics
import subprocess
import sys
from datetime import datetime, timezone

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BENCH_DIR)
CORPUS_DIR = os.path.join(BENCH_DIR, "corpus")
RESULTS_DIR = os.path.join(BENCH_DIR, "results")

# name -> (url, sha256, description)
CORPUS = {
    "attention.pdf": (
        "https://arxiv.org/pdf/1706.03762v7",
        "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
        "arXiv paper, 15 pages, text and vector figures",
    ),
    "latent_diffusion.pdf": (
        "https://arxiv.org/pdf/2112.10752v2",
        "46ede043a8dc07ca1f0f445620523fe1ad8b2436bd83856a3835612a47e9f79e",
        "arXiv paper, 45 pages, dense raster images (41 MB)",
    ),
    "thinkpython2.pdf": (
        "https://greenteapress.com/thinkpython2/thinkpython2.pdf",
        "6033000bac3f9268ad75ee041bf8e5d9a3bc8a33bb156399cbd014918cb59f94",
        "Think Python 2e, 244 pages, book typesetting",
    ),
    "pdf17_spec.pdf": (
        "https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf",
        "9de0ca9e8570d6209e8bd48a355be8eb6ec376acfc3fc3ae97cd8730351417ff",
        "ISO 32000-1 (PDF 1.7) specification, 756 pages, tables and links",
    ),
}


def corpus_path(name):
    path = os.path.join(CORPUS_DIR, name)
    if not os.path.exists(path):
        sys.exit(f"missing {path}; run benchmarks/fetch_corpus.py first")
    return path


def use_checkout(app_path=None, core_path=None):
    """Imports riemann (and optionally riemann_core) from another checkout."""
    if core_path:
        sys.path.insert(0, os.path.abspath(core_path))
    sys.path.insert(0, os.path.abspath(app_path) if app_path else os.path.join(REPO_ROOT, "python-app"))


def isolate_user_dirs(tag):
    """Point XDG dirs at a throwaway folder so benchmarks never touch real settings."""
    base = os.path.join(BENCH_DIR, ".state", tag)
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.environ[key] = os.path.join(base, key.lower())
        os.makedirs(os.environ[key], exist_ok=True)
    os.environ["_RIEMANN_SANITIZED"] = "1"


def _git(*args, cwd=REPO_ROOT):
    try:
        return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def machine_info(app_path=None):
    cpu = platform.processor() or platform.machine()
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    ram_gb = None
    try:
        with open("/proc/meminfo") as f:
            ram_gb = round(int(f.readline().split()[1]) / 1024 / 1024, 1)
    except OSError:
        pass
    os_name = platform.platform()
    try:
        with open("/etc/os-release") as f:
            fields = dict(line.rstrip().split("=", 1) for line in f if "=" in line)
            os_name = f"{fields.get('PRETTY_NAME', '').strip(chr(34))} (kernel {platform.release()})"
    except OSError:
        pass
    checkout = app_path or REPO_ROOT
    info = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "cpu": cpu,
        "logical_cores": os.cpu_count(),
        "ram_gb": ram_gb,
        "os": os_name,
        "python": platform.python_version(),
        "commit": _git("rev-parse", "--short", "HEAD", cwd=checkout),
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=no", cwd=checkout)),
    }
    try:
        import PySide6

        info["pyside6"] = PySide6.__version__
    except ImportError:
        pass
    return info


def median(values):
    return statistics.median(values) if values else float("nan")


def percentile(values, pct):
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def rss_mb(pid=None):
    """Resident set size in MB, read from /proc (Linux) or psutil."""
    try:
        with open(f"/proc/{pid or 'self'}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    import psutil

    return psutil.Process(pid).memory_info().rss / 1024 / 1024


def write_results(name, payload):
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"wrote {os.path.relpath(path, REPO_ROOT)}")
    return path
