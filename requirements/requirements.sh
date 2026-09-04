#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

OUTPUT_IN="requirements.in"
SRC_DIR="../python-app/riemann"   # actual app source to scan for imports
UV_ENV="../riemann"               # uv-managed venv, used only to resolve/pin versions
echo "--> Using uv environment at '$UV_ENV'..."

if [ ! -x "$UV_ENV/bin/python" ]; then
    echo "❌ uv environment not found at '$UV_ENV'"
    exit 1
fi

PYTHON="$UV_ENV/bin/python"

if [ ! -x "$UV_ENV/bin/pipreqs" ]; then
    echo "❌ pipreqs not found in '$UV_ENV' — install it with:"
    echo "    $PYTHON -m pip install pipreqs"
    exit 1
fi

echo "🔍 Scanning '$SRC_DIR' for actual imports with pipreqs..."
TMP_IN="$(mktemp)"
"$UV_ENV/bin/pipreqs" "$SRC_DIR" \
  --print \
  --mode no-pin \
  > "$TMP_IN"

echo "🧩 Correcting known bad import→package mappings (faiss, fitz)..."
# pipreqs has no mapping-table entry for these, so it queries PyPI for a
# package with the literal import name — both of those names are squatted
# by unrelated/placeholder packages, not the real libraries.
#   faiss -> the real package is faiss-cpu (or faiss-gpu if you need GPU support)
#   fitz  -> the real package is PyMuPDF (fitz is just PyMuPDF's import name)
sed -i \
  -e 's/^faiss$/faiss-cpu/' \
  -e 's/^fitz$/PyMuPDF/' \
  "$TMP_IN"

echo "🧹 Filtering out local/compiled modules (riemann, riemann_core, yt-dlp)..."
# pipreqs also has no mapping entry for yt_dlp, so it emits the raw import
# name "yt_dlp" (underscore) rather than the real "yt-dlp" package — match
# both forms so the PyPI-sourced line actually gets dropped.
grep -vE "^riemann(==.*)?$|^riemann_core(==.*)?$|^yt[-_]dlp(==.*)?$" "$TMP_IN" > "$OUTPUT_IN"
rm -f "$TMP_IN"

echo "🔗 Appending latest yt-dlp from git..."
echo "yt-dlp @ git+https://github.com/yt-dlp/yt-dlp.git" >> "$OUTPUT_IN"

echo "📦 Compiling xreqs.txt using pip-compile..."
# NOTE: pip's --no-index is a store_true flag; PIP_NO_INDEX=0 does NOT disable it
# (pip enables the flag whenever the env var is merely present, regardless of value).
# Strip any index-related env vars inherited from the parent shell instead, so
# pip-compile always resolves against the real PyPI index.
env -u PIP_NO_INDEX -u PIP_INDEX_URL -u PIP_EXTRA_INDEX_URL \
  "$PYTHON" -m piptools compile \
  --rebuild \
  --output-file xreqs.txt \
  "$OUTPUT_IN"

echo "✅ Done!"
echo "Generated:"
echo "  - requirements.in (minimal deps, including git yt-dlp)"
echo "  - xreqs.txt (fully pinned deps)"