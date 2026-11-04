"""The fact ledger: every row every page ever said, with where and when it was seen.

Rows are stored once per page version and shared across consecutive snapshots that show the
same content, so history is complete but cheap (a page that changes back to older content is a
new version, dated by the crawl that saw it again). A *view* is the website as it stood at one
snapshot: every page in its newest version at or before that snapshot (a page that drops out
of a later crawl keeps its last known content, as the snapshot README describes).
"""
from __future__ import annotations

import hashlib
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .extract import RawRow, extract_rows
from .pages import AUTHORITY, canonical_url, page_kind, published_date
from .qualifiers import Qualifiers
from .rules import ends_on, page_has_ended


@dataclass(frozen=True)
class PageVersion:
    vid: int
    url: str                 # canonical URL
    raw_url: str             # URL as crawled
    title: str
    kind: str                # pages.page_kind
    published: Optional[str] # byline date on news/blog pages
    fetched_at: str
    first_snapshot: int      # index of the first snapshot containing this version
    ended: bool = False      # the page itself says the offer has ended


@dataclass
class Row:
    rid: int
    kind: str
    subject: str
    text: str
    section: str
    values: tuple
    quals: Qualifiers
    effective: Optional[str]
    links: tuple
    page: PageVersion
    ends: Optional[str] = None   # end date the row states for itself
    label: str = ""
    caption: str = ""
    qual_text: str = ""
    block: int = -1
    pos: int = 0                 # position among the page version's rows

    @property
    def authority(self) -> int:
        kind = self.page.kind
        if kind == "official" and self.kind == "table" and self.effective:
            kind = "rate_sheet"
        return AUTHORITY[kind]

    @property
    def dated(self) -> str:
        """The best date we have for when this statement was made."""
        return self.effective or self.page.published or self.page.fetched_at[:10]


@dataclass
class SnapshotInfo:
    index: int
    snapshot_id: str
    taken_on: str
    pages: dict = field(default_factory=dict)   # canonical url -> vid present in this crawl


class Ledger:
    def __init__(self, extra_rows=None) -> None:
        self.extra_rows = extra_rows       # optional hook: page -> more RawRows (see hooks.py)
        self.snapshots: list[SnapshotInfo] = []
        self.versions: list[PageVersion] = []
        self.rows: list[Row] = []
        self.rows_by_version: dict[int, list[Row]] = {}
        self._latest: dict[str, tuple[str, int]] = {}   # canonical url -> (content hash, vid) of its newest version
        self._views: dict[int, list[Row]] = {}

    # ---- loading -------------------------------------------------------------------------
    def add_snapshot(self, snapshot_id: str, taken_on: str, pages: Iterable) -> SnapshotInfo:
        info = SnapshotInfo(len(self.snapshots), snapshot_id, taken_on)
        for p in sorted(pages, key=lambda p: p.url):
            url = canonical_url(p.url)
            if url in info.pages:
                continue
            digest = hashlib.sha1(p.markdown.encode("utf-8")).hexdigest()
            latest = self._latest.get(url)
            if latest and latest[0] == digest:
                vid = latest[1]
            else:
                vid = self._add_version(url, p, info.index)
                self._latest[url] = (digest, vid)
            info.pages[url] = vid
        self.snapshots.append(info)
        self._views.clear()
        return info

    def _add_version(self, url: str, page, snap_index: int) -> int:
        kind = page_kind(url, page.title)
        published = published_date(page.markdown) if kind in ("news", "blog") else None
        v = PageVersion(len(self.versions), url, page.url, page.title, kind, published, page.fetched_at or "",
                        snap_index, page_has_ended(page.markdown))
        self.versions.append(v)
        rows = []
        raws = extract_rows(page.markdown) + (list(self.extra_rows(page)) if self.extra_rows else [])
        for raw in raws:
            row = self._row(raw, v)
            row.pos = len(rows)
            rows.append(row)
        self.rows_by_version[v.vid] = rows
        return v.vid

    def _row(self, raw: RawRow, v: PageVersion) -> Row:
        row = Row(len(self.rows), raw.kind, raw.subject, raw.text, raw.section, raw.values, raw.quals,
                  raw.effective, raw.links, v, ends_on(raw.text), raw.label, raw.caption, raw.qual_text,
                  raw.block)
        self.rows.append(row)
        return row

    # ---- views ---------------------------------------------------------------------------
    def snapshot_for(self, as_of: Optional[str]) -> int:
        """Index of the snapshot that shows the site as it stood on `as_of` (None = latest)."""
        if not self.snapshots:
            raise ValueError("nothing ingested")
        if as_of is None:
            return len(self.snapshots) - 1
        dates = [s.taken_on for s in self.snapshots]
        return max(0, bisect_right(dates, as_of[:10]) - 1)

    def page_versions(self, snap_index: int) -> dict[str, int]:
        """canonical url -> newest version at or before this snapshot."""
        merged: dict[str, int] = {}
        for s in self.snapshots[: snap_index + 1]:
            merged.update(s.pages)
        return merged

    def view(self, snap_index: int) -> list[Row]:
        if snap_index not in self._views:
            rows: list[Row] = []
            for url, vid in sorted(self.page_versions(snap_index).items()):
                rows.extend(self.rows_by_version[vid])
            self._views[snap_index] = rows
        return self._views[snap_index]

    def in_crawl(self, row: Row, snap_index: int) -> bool:
        """Was this exact page version part of that snapshot's crawl (not carried forward)?"""
        return self.snapshots[snap_index].pages.get(row.page.url) == row.page.vid
