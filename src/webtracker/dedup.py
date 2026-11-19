"""Deterministic deduplication.

Two fingerprints identify a record:

* ``url_fingerprint`` — SHA-256 of the canonical URL (scheme/host lowercased,
  ``www.`` and tracking parameters removed, query sorted, fragment dropped).
* ``content_fingerprint`` — SHA-256 of the normalized title plus the URL's
  domain, catching the same item reached through a different URL on that site.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .sanitize import domain_of, normalize_url
from .schemas import AgentRecord


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def url_fingerprint(url: str) -> str:
    return _sha(normalize_url(url))


def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title).casefold()
    title = re.sub(r"[^\w\s]", " ", title)
    return " ".join(title.split())


def content_fingerprint(title: str, url: str) -> str:
    return _sha(f"{normalize_title(title)}|{domain_of(url) or ''}")


@dataclass
class CandidateRecord:
    """A validated agent record plus its fingerprints and alternate URLs."""

    record: AgentRecord
    url_fp: str
    content_fp: str
    alt_urls: Dict[str, str] = field(default_factory=dict)  # fingerprint -> url

    @classmethod
    def from_agent_record(cls, rec: AgentRecord) -> "CandidateRecord":
        cand = cls(rec, url_fingerprint(rec.url), content_fingerprint(rec.title, rec.url))
        for u in rec.additional_urls:
            fp = url_fingerprint(u)
            if fp != cand.url_fp:
                cand.alt_urls.setdefault(fp, u)
        return cand

    def all_url_fingerprints(self) -> List[str]:
        return [self.url_fp, *self.alt_urls.keys()]


def dedupe_batch(records: List[AgentRecord]) -> List[CandidateRecord]:
    """Collapse duplicates within one agent result, keeping the first occurrence.

    Later duplicates contribute their URLs as alternate sources, and the kept
    record takes the highest relevance score seen.
    """
    kept: List[CandidateRecord] = []
    by_key: Dict[str, CandidateRecord] = {}
    for rec in records:
        cand = CandidateRecord.from_agent_record(rec)
        existing: Optional[CandidateRecord] = None
        for key in (*cand.all_url_fingerprints(), "c:" + cand.content_fp):
            if key in by_key:
                existing = by_key[key]
                break
        if existing is None:
            kept.append(cand)
            target = cand
        else:
            target = existing
            if cand.url_fp != existing.url_fp:
                existing.alt_urls.setdefault(cand.url_fp, rec.url)
            for fp, u in cand.alt_urls.items():
                if fp != existing.url_fp:
                    existing.alt_urls.setdefault(fp, u)
            if rec.relevance_score > existing.record.relevance_score:
                existing.record = existing.record.model_copy(update={"relevance_score": rec.relevance_score})
        for key in (*target.all_url_fingerprints(), "c:" + target.content_fp):
            by_key.setdefault(key, target)
    return kept
