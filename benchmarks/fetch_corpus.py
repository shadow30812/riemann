"""Downloads the benchmark PDFs into benchmarks/corpus and checks their hashes."""

import hashlib
import os
import sys
import urllib.request

from _common import CORPUS, CORPUS_DIR


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    os.makedirs(CORPUS_DIR, exist_ok=True)
    ok = True
    for name, (url, digest, _desc) in CORPUS.items():
        path = os.path.join(CORPUS_DIR, name)
        if not os.path.exists(path):
            print(f"downloading {name} from {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as out:
                out.write(resp.read())
        actual = sha256(path)
        if actual != digest:
            ok = False
            print(f"HASH MISMATCH {name}: expected {digest}, got {actual}")
        else:
            print(f"ok {name}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
