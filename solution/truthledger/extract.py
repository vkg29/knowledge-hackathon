"""Turn one page's markdown into ledger rows.

Three row shapes come out of a page:

* table rows - one per rate/fee table row, with the table caption, qualifier columns (term,
  balance tier, model year, loan amount) and every metric column (rate, APY, APR, ...), plus the
  "Rates are effective as of" date printed under the table;
* hours rows - a branch's lobby / drive-up schedule block;
* text rows - one per prose sentence or bullet, kept with its heading path so prose questions
  (programs, eligibility, contact details) can be answered from the same ledger.

Extraction is deliberately rule-based and deterministic; it never decides what is true, it only
records what each page says.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .qualifiers import Qualifiers, parse_row_qualifiers
from .text import clean_inline, effective_date, has_digit, links, norm, percents, strip_footnote

_SKIP_SECTIONS = re.compile(r"^(handpicked for you|related (posts|articles)|you may also like|recent posts)$", re.I)
_FAQ_LEVEL = 7   # below every markdown heading level
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")
_HOURS_HEAD = re.compile(r"^(lobby|drive[- ]?up|drive[- ]?through|lobby hours|drive-up hours)\s*:?$", re.I)
_DAY = r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*"
_HOURS_LINE = re.compile(rf"^{_DAY}(\s*(-|–|—|through|to|&|and|,)\s*{_DAY})*\s*:", re.I)
_HOURS_INLINE = re.compile(rf"^(lobby|drive[- ]?up)\s+{_DAY}", re.I)

# Column headers that name a qualifier rather than a value.
_QUAL_HEADERS = re.compile(r"(account type|balance tier|tier|term|loan program|program|certificate|card type|"
                           r"product|ira type|category|type|min\.? to (open|earn)|minimum|year|make|vin)", re.I)


@dataclass
class RawRow:
    kind: str                     # "table" | "text" | "hours"
    subject: str                  # what the row is about (caption + row label, or heading path)
    text: str                     # member-facing rendering, also what the matcher reads
    section: str = ""             # heading path on the page
    values: tuple = ()            # ((metric, value), ...) for table rows
    quals: Qualifiers = field(default_factory=Qualifiers)
    effective: Optional[str] = None
    links: tuple = ()             # ((anchor, url), ...) found in the source line
    label: str = ""               # table row label as printed ('17 Month Fixed Certificate Special')
    caption: str = ""             # table caption ('Special Certificates')
    qual_text: str = ""           # qualifier cells as printed ('60 Months')
    block: int = -1               # index of the source line block (sentences of one paragraph share it)


@dataclass
class _Block:
    kind: str                     # "heading" | "table" | "line"
    text: str
    raw: str = ""
    level: int = 0
    rows: list = field(default_factory=list)


def _blocks(markdown: str) -> list[_Block]:
    out: list[_Block] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        if s.startswith("|"):
            table = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table.append(lines[i].strip())
                i += 1
            out.append(_Block("table", "", rows=_parse_table(table)))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            out.append(_Block("heading", clean_inline(m.group(2)), raw=s, level=len(m.group(1))))
        elif s and s not in ("* * *", "✕"):
            out.append(_Block("line", clean_inline(s.lstrip("-*+ ").strip()) if re.match(r"^[-*+]\s", s) else clean_inline(s), raw=s))
        i += 1
    return out


def _parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for ln in lines:
        cells = [clean_inline(c) for c in ln.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c.strip()) or not c.strip() for c in cells) and any("---" in c for c in cells):
            continue
        rows.append(cells)
    return rows


def _metric_name(header: str, cell: str) -> Optional[str]:
    """Name of the value a column holds; the cell's own unit wins over a mislabelled header."""
    h, c = header.lower(), cell.lower()
    if "apr" in c:
        return "APR"
    if "apy" in c:
        return "APY"
    if "annual percentage yield" in h or "apy" in h:
        return "APY"
    if "annual percentage rate" in h or re.search(r"\bapr\b", h):
        return "APR"
    if "dividend" in h:
        return "dividend rate"
    if re.search(r"\brate\b", h):
        return "rate"
    if "fee" in h or "cost" in h or "price" in h:
        return "fee"
    if "payment" in h:
        return "payment example"
    if "point" in h:
        return "points"
    return None


def _table_rows(block: _Block, caption: list[str], section: str, effective: Optional[str]) -> list[RawRow]:
    rows = block.rows
    if len(rows) < 2:
        return []
    header, body = rows[0], rows[1:]
    out = []
    for cells in body:
        cells = [strip_footnote(c) for c in cells]
        if not cells or not any(percents(c) or "$" in c for c in cells):
            continue
        label = cells[0]
        values, qual_cells = [], []
        for j, cell in enumerate(cells[1:], start=1):
            head = header[j] if j < len(header) else ""
            if not cell or cell in ("—", "-"):
                continue
            metric = _metric_name(head, cell) if (percents(cell) or "$" in cell or re.fullmatch(r"\d+(\.\d+)?", cell)) else None
            if metric and not (_QUAL_HEADERS.search(head) and not percents(cell)):
                value = cell if metric != "points" else cell
                value = re.sub(r"\s*(APR|APY)$", "", value).strip()
                values.append((metric, value))
            else:
                qual_cells.append((head, cell))
        if not any(percents(v) for _, v in values) and not any(m == "fee" for m, _ in values):
            continue
        quals = parse_row_qualifiers(label, qual_cells, caption)
        qual_text = ", ".join(c for h, c in qual_cells if not re.search(r"min\.? to", h, re.I))
        subject = " > ".join(caption + [label])
        vtext = ", ".join(f"{v} {m}" if m in ("APY", "APR") else f"{v} {m}" for m, v in values if m not in ("payment example", "points"))
        text = f"{label}{' (' + qual_text + ')' if qual_text else ''}: {vtext}"
        out.append(RawRow("table", subject, text, section, tuple(values), quals, effective,
                          label=label, caption=caption[-1] if caption else "", qual_text=qual_text))
    return out


_ABBREV = re.compile(r"(?:\b[A-Z]|\b(?:St|Ave|Rd|Dr|Ln|Blvd|Hwy|Mr|Mrs|Ms|Jr|Sr|Inc|Co|Corp|Ltd|No|Mt|Ft|vs|etc|U\.S|P\.O))\.$")


def split_sentences(text: str) -> list[str]:
    """Sentence split that keeps "1100 E. Business Hwy." and "P.O. Box" in one piece."""
    out: list[str] = []
    for piece in _SENTENCE_SPLIT.split(text):
        if out and _ABBREV.search(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def _is_link_only(raw: str) -> bool:
    return bool(raw) and not re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", raw).strip(" -*")


def _caption(blocks: list[_Block], idx: int) -> list[str]:
    """Up to two short label lines right above a table ('Credit Cards', 'Platinum Visa')."""
    cap = []
    j = idx - 1
    while j >= 0 and len(cap) < 2:
        b = blocks[j]
        if b.kind == "table":
            break
        if b.kind == "heading":
            if not cap:
                cap.insert(0, b.text)
            break
        t = b.text
        if _is_link_only(b.raw):
            if cap:
                break
            j -= 1
            continue
        if len(t) <= 60 and not t.endswith((".", ":", "!", "?")) and t and not has_digit(t):
            cap.insert(0, t)
        else:
            break
        j -= 1
    return cap


def _table_effective(blocks: list[_Block], idx: int) -> Optional[str]:
    for b in blocks[idx + 1: idx + 5]:
        if b.kind != "line":
            break
        d = effective_date(b.text)
        if d:
            return d
    return None


_LABEL_VALUE = re.compile(r"^(?P<label>[A-Za-z0-9][^:]{2,80}):\s*(?P<value>.{1,90})$")


def _label_value_row(text: str, heads: list, section: str) -> Optional[RawRow]:
    """A 'Label: value' line that states a rate or fee ('6-month certificate APY: 3.50%') is a
    one-row table: it becomes a ledger row with the same qualifiers and metrics as table rows."""
    m = _LABEL_VALUE.match(text)
    if not m:
        return None
    label, value = m.group("label").strip(), m.group("value").strip()
    pct = percents(value)
    money = re.search(r"\$\s?\d[\d,]*(?:\.\d+)?", value)
    if not pct and not money:
        return None
    words = f"{label} {value}"
    metric = _metric_name(words, value) or ("fee" if money and not pct else "rate")
    figure = pct[0] if pct else money.group(0)
    rest = _SPACE_RE.sub(" ", value.replace(figure, " ")).strip(" ,;")
    caption = heads[-1][1] if heads else ""
    quals = parse_row_qualifiers(f"{label} {rest}", [], [caption] if caption else [])
    return RawRow("table", " > ".join(x for x in (caption, label) if x), text, section, ((metric, figure),), quals,
                  effective_date(text), label=label, caption=caption, qual_text=rest[:60])


_SPACE_RE = re.compile(r"\s+")


def extract_rows(markdown: str) -> list[RawRow]:
    blocks = _blocks(markdown)
    out: list[RawRow] = []
    heads: list[tuple[int, str]] = []
    skipping_level = 0
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.kind == "heading":
            if skipping_level and b.level > skipping_level:
                i += 1
                continue
            skipping_level = 0
            if _SKIP_SECTIONS.match(b.text.strip()):
                skipping_level = b.level
                i += 1
                continue
            text = b.text
            prev = blocks[i - 1] if i else None
            if prev is not None and prev.kind == "heading" and prev.level > b.level and heads and heads[-1][1] == prev.text:
                text = f"{prev.text}: {text}"   # small "eyebrow" heading right above a big one labels it
            heads = [h for h in heads if h[0] < b.level] + [(b.level, text)]
            i += 1
            continue
        if skipping_level:
            i += 1
            continue
        if b.kind == "line" and _is_link_only(b.raw) and b.text.endswith("?") and re.match(r"^\s*[-*+]", b.raw):
            heads = [h for h in heads if h[0] < _FAQ_LEVEL] + [(_FAQ_LEVEL, b.text)]
            i += 1
            continue
        section = " > ".join(t for _, t in heads)
        if b.kind == "table":
            out.extend(_table_rows(b, _caption(blocks, i), section, _table_effective(blocks, i)))
            i += 1
            continue
        # Branch hours: a "Lobby" / "Drive-up" label followed by day: time lines.
        if (_HOURS_HEAD.match(b.text) or _HOURS_INLINE.match(b.text)
                or (_HOURS_LINE.match(b.text) and i + 1 < len(blocks) and _HOURS_LINE.match(blocks[i + 1].text))):
            j, parts, current = i, [], None
            while j < len(blocks) and blocks[j].kind == "line" and (_HOURS_HEAD.match(blocks[j].text) or _HOURS_LINE.match(blocks[j].text)
                                                                     or re.match(r"^(lobby|drive[- ]?up)\b.*:\s*$|^(lobby|drive[- ]?up)\s+" + _DAY, blocks[j].text, re.I)):
                t = blocks[j].text
                m = re.match(r"^(lobby|drive[- ]?up|drive[- ]?through)(?: hours)?\s*:?\s*(.*)$", t, re.I)
                if m:
                    current = m.group(1).capitalize().replace("Drive up", "Drive-up")
                    parts.append([current, []])
                    if m.group(2):
                        parts[-1][1].append(m.group(2))
                else:
                    if not parts:
                        parts.append(["Hours", []])
                    parts[-1][1].append(t)
                j += 1
            if any(p[1] for p in parts):
                text = "; ".join(f"{name}: {', '.join(days)}" for name, days in parts if days)
                text = text.replace("–", "-").replace("—", "-")
                out.append(RawRow("hours", section, text, section))
                i = j
                continue
        if b.kind == "line" and _is_link_only(b.raw) and out and out[-1].kind == "text" and out[-1].section == section:
            # A button line ("[Join Dupaco](https://join.dupaco.com/)") belongs to the text above it.
            out[-1].links = out[-1].links + tuple(links(b.raw))
            i += 1
            continue
        if b.kind == "line":
            fact = _label_value_row(b.text, heads, section)
            if fact:
                out.append(fact)
            lk = tuple(links(b.raw))
            for sent in split_sentences(b.text):
                sent = sent.strip()
                if len(sent) < 12 and not has_digit(sent):
                    continue
                if len(sent.split()) < 3 and not lk:
                    continue
                out.append(RawRow("text", section, sent, section, links=lk, effective=effective_date(sent), block=i))
        i += 1
    return out
