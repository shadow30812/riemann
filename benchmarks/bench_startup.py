"""Measures cold start of the full app: launch to window, and launch to a sharp first page.

Each run starts a fresh Python process that runs Riemann (`riemann.app.run`)
with one PDF on the command line, `--new-window`, and throwaway settings
folders. The parent notes the time just before spawning; the child reports when
its imports finished, when the main window was shown, and when the first page
of the PDF was on screen at full resolution, plus its memory use at that point.

The first run starts from empty data and cache folders, so it includes one-time
work (creating the private virtual environment for optional dependencies,
building font caches); it is reported separately as `first_launch`. Later runs
keep the data and cache folders but start with empty settings, so no session is
restored.

    env -u LD_LIBRARY_PATH riemann/bin/python benchmarks/bench_startup.py
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from _common import BENCH_DIR, REPO_ROOT, corpus_path, machine_info, median, write_results


def child(doc):
    t_start = float(os.environ["BENCH_T0"])
    from PySide6.QtCore import QTimer

    import riemann.app as A
    from _common import rss_mb

    marks = {"imports_ms": (time.time() - t_start) * 1000}
    orig_show = A.RiemannWindow.show

    def show(self):
        orig_show(self)
        marks.setdefault("window_ms", (time.time() - t_start) * 1000)

        def poll():
            tab = self.tabs_main.currentWidget()
            widgets = getattr(tab, "page_widgets", {}) or {}
            w = widgets.get(getattr(tab, "current_page_index", 0))
            # Older versions had no stand-ins: any pixmap on screen was the real page.
            sharp = w is not None and getattr(w, "has_full_page", not w.pixmap().isNull())
            if sharp:
                marks["first_page_ms"] = (time.time() - t_start) * 1000
                marks["rss_mb"] = rss_mb()
                print("BENCH " + json.dumps(marks), flush=True)
                os._exit(0)
            QTimer.singleShot(2, poll)

        QTimer.singleShot(0, poll)

    A.RiemannWindow.show = show
    sys.argv = ["riemann", "--new-window", doc]
    A.run()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--doc", default="pdf17_spec.pdf")
    ap.add_argument("--platform", default="offscreen", help="Qt platform plugin for the child")
    ap.add_argument("--app-path", default=None, help="python-app folder of the checkout to test")
    ap.add_argument("--core-path", default=None, help="folder holding a riemann_core package to use")
    ap.add_argument("--label", default="current")
    ap.add_argument("--child", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        child(args.child)
        return

    doc = corpus_path(args.doc)
    state = os.path.join(BENCH_DIR, ".state", f"startup-{args.label}")
    env = dict(os.environ)
    app_path = os.path.abspath(args.app_path) if args.app_path else os.path.join(REPO_ROOT, "python-app")
    paths = [app_path, BENCH_DIR]
    if args.core_path:
        paths.insert(0, os.path.abspath(args.core_path))
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["QT_QPA_PLATFORM"] = args.platform
    env["_RIEMANN_SANITIZED"] = "1"
    env.pop("LD_LIBRARY_PATH", None)

    shutil.rmtree(state, ignore_errors=True)
    runs, first_launch = [], None
    for i in range(args.runs + 1):
        for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            env[key] = os.path.join(state, key.lower())
            if key == "XDG_CONFIG_HOME":
                shutil.rmtree(env[key], ignore_errors=True)
            os.makedirs(env[key], exist_ok=True)
        env["BENCH_T0"] = repr(time.time())
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child", doc],
            env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
        line = next((ln for ln in out.stdout.splitlines() if ln.startswith("BENCH ")), None)
        if line is None:
            sys.exit(f"child failed:\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}")
        marks = json.loads(line[6:])
        label = "first launch" if i == 0 else f"run {i}"
        print(
            f"{label:12}: imports {marks['imports_ms']:6.0f} ms | window {marks['window_ms']:6.0f} ms | "
            f"first sharp page {marks['first_page_ms']:6.0f} ms | RSS {marks['rss_mb']:5.0f} MB"
        )
        if i == 0:
            first_launch = marks
        else:
            runs.append(marks)

    summary = {k: round(median([r[k] for r in runs]), 1) for k in runs[0]}
    print("median:", summary)
    write_results(
        f"startup-{args.label}",
        {
            "label": args.label,
            "machine": machine_info(os.path.dirname(app_path)),
            "settings": {"runs": args.runs, "doc": args.doc, "platform": args.platform},
            "median": summary,
            "first_launch": first_launch,
            "runs": runs,
        },
    )


if __name__ == "__main__":
    main()
