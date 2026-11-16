"""
Exporting the library's metadata as a bibliography.

Two formats: BibTeX (.bib) for LaTeX, and CSL-JSON (.json), which Zotero,
Mendeley, Pandoc and most reference managers import directly. Entries come
from LibraryManager rows (title, authors, year, doi, arxiv_id, file_path).

Authors are stored as one string, usually "First Last, First Last"; the
parser also accepts "Last, First and Last, First". Citation keys follow the
common lastname + year + first title word pattern ("vaswani2017attention"),
with a, b, c... added when two papers would share a key.
"""

import json
import re
import unicodedata
from typing import Any, Dict, List
from .endpoints import arxiv_url

STOPWORDS = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "towards", "toward"}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def split_authors(authors: str) -> List[Dict[str, str]]:
    """Authors as [{"family": ..., "given": ...}], from either common way of writing them."""
    text = (authors or "").strip()
    if not text:
        return []
    if " and " in text:
        parts = [p.strip() for p in text.split(" and ") if p.strip()]
        out = []
        for part in parts:
            if "," in part:
                family, given = (s.strip() for s in part.split(",", 1))
            else:
                bits = part.split()
                family, given = bits[-1], " ".join(bits[:-1])
            out.append({"family": family, "given": given})
        return out
    out = []
    for name in (n.strip() for n in text.split(",")):
        if not name:
            continue
        bits = name.split()
        out.append({"family": bits[-1], "given": " ".join(bits[:-1])})
    return out


def citation_key(entry: Dict[str, Any]) -> str:
    authors = split_authors(str(entry.get("authors") or ""))
    family = re.sub(r"[^a-z]", "", _ascii(authors[0]["family"]).lower()) if authors else "anon"
    year = re.sub(r"\D", "", str(entry.get("year") or ""))[:4]
    words = [re.sub(r"[^a-z0-9]", "", _ascii(w).lower()) for w in str(entry.get("title") or "").split()]
    word = next((w for w in words if w and w not in STOPWORDS), "")
    return f"{family or 'anon'}{year}{word}"


def _unique_keys(entries: List[Dict[str, Any]]) -> List[str]:
    keys, seen = [], {}
    for entry in entries:
        base = citation_key(entry)
        n = seen.get(base, 0)
        seen[base] = n + 1
        keys.append(base if n == 0 else base + "abcdefghijklmnopqrstuvwxyz"[min(n - 1, 25)])
    return keys


def _bib_escape(value: str) -> str:
    value = value.replace("\\", "\\textbackslash{}")
    for ch in "&%$#_":
        value = value.replace(ch, "\\" + ch)
    return value.replace("{", "\\{").replace("}", "\\}")


def to_bibtex(entries: List[Dict[str, Any]]) -> str:
    """The entries as a BibTeX file."""
    out = []
    for key, entry in zip(_unique_keys(entries), entries):
        arxiv = str(entry.get("arxiv_id") or "").strip()
        fields = []
        title = str(entry.get("title") or "").strip()
        if title:
            fields.append(("title", "{" + _bib_escape(title) + "}"))
        authors = split_authors(str(entry.get("authors") or ""))
        if authors:
            fields.append(("author", _bib_escape(" and ".join(f"{a['family']}, {a['given']}".rstrip(", ") for a in authors))))
        year = re.sub(r"\D", "", str(entry.get("year") or ""))[:4]
        if year:
            fields.append(("year", year))
        doi = str(entry.get("doi") or "").strip()
        if doi:
            fields.append(("doi", _bib_escape(doi)))
        if arxiv:
            fields += [("eprint", arxiv), ("archivePrefix", "arXiv"), ("url", arxiv_url(arxiv))]
        path = str(entry.get("file_path") or "").strip()
        if path:
            fields.append(("file", _bib_escape(path)))
        kind = "misc" if arxiv and not doi else "article"
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        out.append(f"@{kind}{{{key},\n{body}\n}}")
    return "\n\n".join(out) + ("\n" if out else "")


def to_csl_json(entries: List[Dict[str, Any]]) -> str:
    """The entries as CSL-JSON, the format Zotero and Pandoc import."""
    items = []
    for key, entry in zip(_unique_keys(entries), entries):
        arxiv = str(entry.get("arxiv_id") or "").strip()
        item: Dict[str, Any] = {"id": key, "type": "article-journal" if entry.get("doi") else "article"}
        if entry.get("title"):
            item["title"] = str(entry["title"]).strip()
        authors = split_authors(str(entry.get("authors") or ""))
        if authors:
            item["author"] = authors
        year = re.sub(r"\D", "", str(entry.get("year") or ""))[:4]
        if year:
            item["issued"] = {"date-parts": [[int(year)]]}
        if entry.get("doi"):
            item["DOI"] = str(entry["doi"]).strip()
        if arxiv:
            item["URL"] = arxiv_url(arxiv)
            item["number"] = f"arXiv:{arxiv}"
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False) + "\n"
