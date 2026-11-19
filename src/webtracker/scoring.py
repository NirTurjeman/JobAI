"""Evaluate agent records against the configurable criteria."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .config import ResearchConfig
from .schemas import AgentRecord


@dataclass
class ScoreResult:
    score: float
    agent_score: float
    excluded: bool
    criteria: Dict[str, Dict[str, object]] = field(default_factory=dict)
    explanation: str = ""

    def qualifies(self, threshold: float) -> bool:
        return not self.excluded and self.score >= threshold


def _matches(keyword: str, text: str) -> bool:
    # Word-boundary match so short keywords do not hit inside other words.
    return re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", text) is not None


def evaluate(record: AgentRecord, cfg: ResearchConfig) -> ScoreResult:
    text = " ".join(
        [record.title, record.description, *(f"{m.key} {m.value}" for m in record.metadata)]
    ).casefold()

    agent_score = float(record.relevance_score)
    breakdown: Dict[str, Dict[str, object]] = {}
    excluded_by: List[str] = []
    weighted_hits = 0.0
    weight_total = 0.0

    for crit in cfg.criteria:
        hits = [k for k in crit.include_keywords if _matches(k, text)]
        blocks = [k for k in crit.exclude_keywords if _matches(k, text)]
        entry: Dict[str, object] = {"matched": hits, "excluded_by": blocks, "weight": crit.weight}
        if blocks:
            excluded_by.append(crit.name)
        if crit.include_keywords:
            weight_total += crit.weight
            if hits:
                weighted_hits += crit.weight
            elif crit.required:
                excluded_by.append(crit.name)
                entry["required_unmet"] = True
        breakdown[crit.name] = entry

    if weight_total > 0:
        keyword_score = weighted_hits / weight_total
        w = cfg.scoring.agent_weight
        score = w * agent_score + (1 - w) * keyword_score
    else:
        score = agent_score
    score = round(max(0.0, min(1.0, score)), 4)

    parts = []
    if record.explanation:
        parts.append(record.explanation)
    matched = [name for name, e in breakdown.items() if e["matched"]]
    if matched:
        parts.append("Matched criteria: " + ", ".join(matched) + ".")
    if excluded_by:
        parts.append("Excluded by: " + ", ".join(sorted(set(excluded_by))) + ".")

    return ScoreResult(
        score=score,
        agent_score=agent_score,
        excluded=bool(excluded_by),
        criteria=breakdown,
        explanation=" ".join(parts),
    )
