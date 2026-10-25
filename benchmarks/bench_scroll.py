"""Measures what the reader shows while scrolling fast, and how long the UI thread blocks.

A ReaderTab in a 1400x1000 window (Fit Width, so pages render at about 2.25x)
is scrolled at a fixed speed with 60 ticks per second. After every tick the
script records whether the page in the middle of the viewport is sharp (full
resolution), a low-resolution stand-in, or blank. It also records the longest
single pass of the Qt event loop, which is how long the interface froze.

The same script runs against any checkout, so an older version can be compared
with the current one:

    env -u LD_LIBRARY_PATH riemann/bin/python benchmarks/bench_scroll.py
    env -u LD_LIBRARY_PATH riemann/bin/python benchmarks/bench_scroll.py \\
        --app-path /path/to/old-checkout/python-app --core-path /path/to/old-core \\
        --label before

Uses Qt's offscreen platform by default (device pixel ratio 1).
"""

import argparse
import os
import shutil
import sys
import time

from _common import BENCH_DIR, corpus_path, machine_info, median, percentile, use_checkout, write_results

TICK_S = 1 / 60


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--app-path", default=None, help="python-app folder of the checkout to test")
    ap.add_argument("--core-path", default=None, help="folder holding a riemann_core package to use")
    ap.add_argument("--label", default="current")
    ap.add_argument("--docs", default="pdf17_spec.pdf,latent_diffusion.pdf")
    ap.add_argument("--speeds", default="2000,6000,19000", help="scroll speeds in px/s")
    ap.add_argument("--seconds", type=float, default=5.0, help="scroll time per run")
    ap.add_argument("--runs", type=int, default=3)
    return ap.parse_args()


ARGS = parse_args()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
use_checkout(ARGS.app_path, ARGS.core_path)

from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

app = QApplication(sys.argv)


class Pump:
    """Runs the Qt event loop for a while, recording the longest single pass."""

    def __init__(self):
        self.passes = []

    def until(self, deadline):
        while True:
            t = time.perf_counter()
            app.processEvents()
            self.passes.append(time.perf_counter() - t)
            if time.perf_counter() >= deadline:
                return
            time.sleep(0.001)

    def for_seconds(self, seconds):
        self.until(time.perf_counter() + seconds)


def page_state(tab):
    vbar = tab.scroll.verticalScrollBar()
    centre = vbar.value() + tab.scroll.viewport().height() // 2
    w = tab.page_widgets.get(tab._get_closest_page(centre))
    if w is None or w.pixmap() is None or w.pixmap().isNull():
        return "blank"
    # Older versions had no stand-ins: any pixmap on screen was the real page.
    return "sharp" if getattr(w, "has_full_page", True) else "standin"


def run_once(doc_path, speed):
    from riemann.ui.reader.tab import ReaderTab

    state_dir = os.path.join(BENCH_DIR, ".state", f"scroll-{ARGS.label}")
    shutil.rmtree(state_dir, ignore_errors=True)
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.environ[key] = os.path.join(state_dir, key.lower())
        os.makedirs(os.environ[key], exist_ok=True)
    os.environ["_RIEMANN_SANITIZED"] = "1"

    pump = Pump()
    win = QMainWindow()
    tab = ReaderTab()
    win.setCentralWidget(tab)
    win.resize(1400, 1000)
    win.show()
    pump.for_seconds(0.3)

    t_open = time.perf_counter()
    tab.load_document(doc_path)
    tab.apply_zoom_string("Fit Width")
    first_sharp_ms = None
    while time.perf_counter() - t_open < 15:
        pump.for_seconds(0.002)
        if tab.current_doc is not None and tab.page_widgets and page_state(tab) == "sharp":
            first_sharp_ms = (time.perf_counter() - t_open) * 1000
            break
    pump.for_seconds(1.0)  # let the initial layout and neighbour renders settle

    vbar = tab.scroll.verticalScrollBar()
    step = speed * TICK_S
    states = {"sharp": 0, "standin": 0, "blank": 0}
    pump.passes = []
    position = float(vbar.value())
    start = time.perf_counter()
    ticks = 0
    while ticks * TICK_S < ARGS.seconds and vbar.value() < vbar.maximum() - step:
        position += step
        vbar.setValue(int(position))
        pump.until(start + (ticks + 1) * TICK_S)
        states[page_state(tab)] += 1
        ticks += 1
    scroll_passes = pump.passes

    t_stop = time.perf_counter()
    settle_ms = None
    while time.perf_counter() - t_stop < 10:
        pump.for_seconds(0.002)
        if page_state(tab) == "sharp":
            settle_ms = (time.perf_counter() - t_stop) * 1000
            break

    if hasattr(tab, "cleanup"):
        tab.cleanup()
    win.close()
    win.deleteLater()
    pump.for_seconds(0.2)

    return {
        "ticks": ticks,
        "pct": {k: 100 * v / max(ticks, 1) for k, v in states.items()},
        "ui_pass_max_ms": max(scroll_passes) * 1000,
        "ui_pass_p99_ms": percentile(scroll_passes, 99) * 1000,
        "settle_ms": settle_ms,
        "first_sharp_ms": first_sharp_ms,
    }


def main():
    speeds = [int(s) for s in ARGS.speeds.split(",")]
    results = {
        "label": ARGS.label,
        "machine": machine_info(os.path.dirname(os.path.abspath(ARGS.app_path)) if ARGS.app_path else None),
        "settings": {**vars(ARGS), "window": "1400x1000", "zoom": "Fit Width", "platform": app.platformName()},
        "documents": {},
    }
    for name in ARGS.docs.split(","):
        path = corpus_path(name)
        per_speed = {}
        for speed in speeds:
            runs = [run_once(path, speed) for _ in range(ARGS.runs)]
            agg = {
                "runs": len(runs),
                "ticks": int(median([r["ticks"] for r in runs])),
                "sharp_pct": round(median([r["pct"]["sharp"] for r in runs]), 1),
                "standin_pct": round(median([r["pct"]["standin"] for r in runs]), 1),
                "blank_pct": round(median([r["pct"]["blank"] for r in runs]), 1),
                "ui_pass_max_ms": round(median([r["ui_pass_max_ms"] for r in runs]), 1),
                "ui_pass_p99_ms": round(median([r["ui_pass_p99_ms"] for r in runs]), 1),
                "settle_ms": round(median([r["settle_ms"] or float("inf") for r in runs]), 1),
                "first_sharp_ms": round(median([r["first_sharp_ms"] or float("inf") for r in runs]), 1),
            }
            per_speed[str(speed)] = agg
            print(
                f"[{ARGS.label}] {name:22} {speed:6} px/s | sharp {agg['sharp_pct']:5.1f}% "
                f"stand-in {agg['standin_pct']:5.1f}% blank {agg['blank_pct']:5.1f}% | "
                f"longest UI block {agg['ui_pass_max_ms']:6.1f} ms (p99 {agg['ui_pass_p99_ms']:5.1f}) | "
                f"sharp {agg['settle_ms']:6.1f} ms after stop | first page {agg['first_sharp_ms']:6.1f} ms"
            )
        results["documents"][name] = per_speed
    write_results(f"scroll-{ARGS.label}", results)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # skip Qt's teardown of leftover worker threads in older checkouts
