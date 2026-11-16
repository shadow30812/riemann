"""
References behind citation links, and looking them up on OpenAlex.

In a paper, a citation such as "[12]" is usually an internal link to the
reference list: "#page=11&top=453.45" names the page and the height (in PDF
points from the bottom) where entry 12 starts. reference_text_at reads the
characters from that height down, line by line, until the next entry starts
(a line opening with "[n]" or "n."), a large vertical gap, or about 600
characters.

lookup asks OpenAlex (a free, open catalogue of scholarly works) about one
reference: by arXiv id or DOI when the text holds one, otherwise by its
guessed title within a year of its date, keeping only a close title match
(a loose full-text search happily returns a tutorial about the cited paper). It only runs when the user asks, since it sends the
reference to openalex.org. best_link picks where to read the work: its arXiv
page, an open-access copy, or its DOI.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from .endpoints import OPENALEX_WORKS, USER_AGENT, arxiv_url, doi_url

OPENALEX = OPENALEX_WORKS

ENTRY_START = re.compile(r"^\s*(\[\d{1,4}\]|\d{1,4}\.\s|\(\d{1,4}\))")
ARXIV = re.compile(r"(?:arXiv:|arxiv\.org/abs/)\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;])", re.I)
YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _lines(chars: List[Tuple[str, Tuple[float, float, float, float]]]) -> List[Tuple[float, float, str]]:
    """(top, bottom, text) per text line, top to bottom, from PDFium character boxes."""
    lines: List[List[Any]] = []
    for ch, (left, top, right, bottom) in chars:
        if top == bottom == 0 and ch not in " \r\n":
            continue
        hi, lo = max(top, bottom), min(top, bottom)
        if ch in "\r\n":
            if lines:
                lines[-1][2] += " "
            continue
        if lines and abs((lines[-1][0] + lines[-1][1]) / 2 - (hi + lo) / 2) < max(2.0, (hi - lo) * 0.6):
            line = lines[-1]
            line[0], line[1] = max(line[0], hi), min(line[1], lo)
            line[2] += ch
        else:
            lines.append([hi, lo, ch])
    return [(t, b, " ".join(text.split())) for t, b, text in lines if text.strip()]


def reference_text_at(doc, page: int, top: Optional[float], limit: int = 600) -> str:
    """The reference entry that starts at top on page (or the page's first lines)."""
    try:
        lines = _lines(doc.get_char_boxes(page))
    except Exception:
        return ""
    if not lines:
        return ""
    start = 0
    if top is not None:
        for i, (line_top, line_bottom, _) in enumerate(lines):
            if line_top <= top + 3:
                start = i
                break
    out: List[str] = []
    prev_bottom = None
    height = None
    for i in range(start, len(lines)):
        line_top, line_bottom, text = lines[i]
        line_h = max(1.0, line_top - line_bottom)
        height = height or line_h
        if out and ENTRY_START.match(text):
            break
        if prev_bottom is not None and prev_bottom - line_top > height * 1.6:
            break
        out.append(text)
        prev_bottom = line_bottom
        if sum(len(t) for t in out) >= limit:
            break
    joined = " ".join(out)
    joined = re.sub(r"(\w)- (\w)", r"\1\2", joined)
    return joined[:limit].strip()


def looks_like_reference(text: str) -> bool:
    return bool(text) and (bool(YEAR.search(text)) or bool(ARXIV.search(text)) or bool(DOI.search(text)))


def _search_terms(reference: str) -> str:
    text = ENTRY_START.sub("", reference)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s'-]", " ", text)
    return " ".join(text.split())[:300]


def _summary(work: Dict[str, Any]) -> Dict[str, Any]:
    authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])]
    ids = work.get("ids") or {}
    arxiv = ""
    for loc in work.get("locations") or []:
        url = str((loc or {}).get("landing_page_url") or "")
        m = ARXIV.search(url)
        if m:
            arxiv = m.group(1)
            break
    oa = (work.get("open_access") or {}).get("oa_url") or ""
    return {
        "title": work.get("title") or work.get("display_name") or "",
        "year": work.get("publication_year"),
        "authors": [a for a in authors if a],
        "cited_by": int(work.get("cited_by_count") or 0),
        "doi": str(ids.get("doi") or work.get("doi") or "").replace(doi_url(""), ""),
        "arxiv": arxiv,
        "open_access_url": oa,
        "openalex": work.get("id", ""),
    }


def guess_title_and_year(reference: str) -> Tuple[str, Optional[int]]:
    """
    The likely title and year of a reference in the usual "Authors. Title.
    Venue, year." shape: the first sentence after the authors (which are the
    sentence full of commas and "and"), and the last year mentioned.
    """
    text = ENTRY_START.sub("", reference).strip()
    years = [int(m.group(0)) for m in YEAR.finditer(text)]
    parts = [p.strip() for p in re.split(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z0-9])", text) if p.strip()]
    title = ""
    if len(parts) >= 2:
        first = parts[0]
        authorish = first.count(",") >= 1 or " and " in first or len(first.split()) <= 4
        title = parts[1] if authorish else first
    elif parts:
        title = parts[0]
    return title.strip(" .,"), (years[-1] if years else None)


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    norm = lambda t: " ".join(re.sub(r"[^\w\s]", " ", t.lower()).split())
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def lookup(reference: str, session: Optional[requests.Session] = None, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    """
    What OpenAlex knows about the cited work, or None when nothing matches
    well. Exact identifiers win; otherwise the guessed title is searched among
    titles of that year, and a result counts only if its title is close.
    """
    http = session or requests
    headers = {"User-Agent": USER_AGENT}
    m = ARXIV.search(reference)
    d = DOI.search(reference)
    try:
        if m:
            r = http.get(f"{OPENALEX}/arxiv:{m.group(1)}", headers=headers, timeout=timeout)
            if r.status_code == 200:
                return _summary(r.json())
        if d:
            r = http.get(f"{OPENALEX}/doi:{d.group(1)}", headers=headers, timeout=timeout)
            if r.status_code == 200:
                return _summary(r.json())
        title, year = guess_title_and_year(reference)
        if len(title) < 8:
            return None
        clean = re.sub(r"[,:|]", " ", title)
        filters = f"title.search:{clean}" + (f",publication_year:{year - 1}-{year + 1}" if year else "")
        r = http.get(OPENALEX, params={"filter": filters, "per-page": 5}, headers=headers, timeout=timeout)
        candidates = r.json().get("results") or [] if r.status_code == 200 else []
        if not candidates:
            r = http.get(OPENALEX, params={"search": _search_terms(reference), "per-page": 5}, headers=headers, timeout=timeout)
            candidates = r.json().get("results") or [] if r.status_code == 200 else []
        scored = sorted(
            ((_similar(title, str(w.get("title") or "")), w) for w in candidates),
            key=lambda pair: (-pair[0], -int(pair[1].get("cited_by_count") or 0)),
        )
        if scored and scored[0][0] >= 0.75:
            return _summary(scored[0][1])
        return None
    except (requests.RequestException, ValueError):
        return None


def best_link(info: Dict[str, Any]) -> str:
    if info.get("arxiv"):
        return arxiv_url(info["arxiv"])
    if info.get("open_access_url"):
        return info["open_access_url"]
    if info.get("doi"):
        return doi_url(info["doi"])
    return str(info.get("openalex") or "")
