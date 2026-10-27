"""Deterministic buyer-signal extraction and Markdown report rendering."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .models import Comment, ResearchDataset


MAX_QUOTE_WORDS = 24

CATEGORY_TERMS = {
    "pains": (
        "annoying",
        "double entry",
        "frustrated",
        "hard to",
        "headache",
        "manual",
        "missed",
        "no show",
        "problem",
        "takes forever",
        "waste",
    ),
    "triggers": (
        "after",
        "because",
        "finally",
        "needed",
        "signed up",
        "switched",
        "when",
    ),
    "objections": (
        "annual contract",
        "concerned",
        "expensive",
        "migration",
        "month to month",
        "price",
        "risky",
        "training",
        "worry",
        "would not",
        "won't",
    ),
}

POSITIVE_TERMS = (
    "easy",
    "good",
    "helpful",
    "like",
    "love",
    "reliable",
    "simple",
    "works",
)
NEGATIVE_TERMS = (
    "annoying",
    "bad",
    "confusing",
    "expensive",
    "frustrating",
    "hard",
    "missing",
    "problem",
    "slow",
)

STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "but",
    "by",
    "can",
    "do",
    "for",
    "from",
    "get",
    "had",
    "has",
    "have",
    "i",
    "if",
    "in",
    "is",
    "it",
    "just",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "they",
    "this",
    "to",
    "up",
    "was",
    "we",
    "were",
    "when",
    "with",
    "would",
    "you",
}


@dataclass(frozen=True)
class Evidence:
    quote: str
    thread_title: str
    thread_url: str
    score: int
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class CompetitorFinding:
    name: str
    mentions: int
    sentiment: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Analysis:
    dataset: ResearchDataset
    pains: tuple[Evidence, ...]
    triggers: tuple[Evidence, ...]
    objections: tuple[Evidence, ...]
    language: tuple[tuple[str, int], ...]
    competitors: tuple[CompetitorFinding, ...]

    def summary_payload(self) -> dict[str, object]:
        return {
            "niche": self.dataset.niche,
            "pains": [item.quote for item in self.pains],
            "buying_triggers": [item.quote for item in self.triggers],
            "objections": [item.quote for item in self.objections],
            "customer_language": [phrase for phrase, _ in self.language],
            "competitors": [
                {
                    "name": item.name,
                    "mentions": item.mentions,
                    "sentiment": item.sentiment,
                }
                for item in self.competitors
            ],
        }


def _normalized_text(value: str) -> str:
    return re.sub(r"[^a-z0-9' ]+", " ", value.lower()).strip()


def _has_term(value: str, term: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9']){re.escape(term.lower())}(?![a-z0-9'])",
            value.lower(),
        )
    )


def _excerpt(value: str, max_words: int = MAX_QUOTE_WORDS) -> str:
    clean = " ".join(value.split())
    words = clean.split()
    if len(words) <= max_words:
        return clean
    return " ".join(words[:max_words]).rstrip(".,;:") + "..."


def _escape_markdown(value: str) -> str:
    """Render collected text as text, not as active Markdown or HTML."""
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_[\]{}|])", r"\\\1", escaped)


def _evidence_for(comments: Iterable[Comment], terms: tuple[str, ...]) -> tuple[Evidence, ...]:
    ranked: list[Evidence] = []
    seen: set[str] = set()
    for comment in comments:
        normalized = _normalized_text(comment.body)
        matches = tuple(term for term in terms if _has_term(normalized, term))
        fingerprint = normalized[:240]
        if not matches or fingerprint in seen:
            continue
        seen.add(fingerprint)
        ranked.append(
            Evidence(
                quote=_excerpt(comment.body),
                thread_title=comment.thread_title,
                thread_url=comment.thread_url,
                score=comment.score + (10 * len(matches)),
                matched_terms=matches,
            )
        )
    return tuple(sorted(ranked, key=lambda item: item.score, reverse=True)[:5])


def _customer_language(comments: Iterable[Comment]) -> tuple[tuple[str, int], ...]:
    phrase_counts: Counter[str] = Counter()
    phrase_documents: Counter[str] = Counter()
    for comment in comments:
        tokens = re.findall(r"[a-z0-9']+", comment.body.lower())
        seen_in_comment: set[str] = set()
        for width in (2, 3, 4):
            for index in range(len(tokens) - width + 1):
                phrase_tokens = tokens[index : index + width]
                if phrase_tokens[0] in STOPWORDS or phrase_tokens[-1] in STOPWORDS:
                    continue
                if sum(token not in STOPWORDS for token in phrase_tokens) < 2:
                    continue
                phrase = " ".join(phrase_tokens)
                phrase_counts[phrase] += 1
                seen_in_comment.add(phrase)
        phrase_documents.update(seen_in_comment)

    candidates = [
        (phrase, count)
        for phrase, count in phrase_counts.items()
        if count >= 2 and phrase_documents[phrase] >= 2
    ]
    candidates.sort(key=lambda item: (item[1] * len(item[0].split()), item[1], item[0]), reverse=True)

    selected: list[tuple[str, int]] = []
    for phrase, count in candidates:
        if any(phrase in chosen or chosen in phrase for chosen, _ in selected):
            continue
        selected.append((phrase, count))
        if len(selected) == 6:
            break
    return tuple(selected)


def _competitor_findings(
    comments: Iterable[Comment], competitors: Iterable[str]
) -> tuple[CompetitorFinding, ...]:
    findings: list[CompetitorFinding] = []
    comments = tuple(comments)
    for competitor in competitors:
        matching = [
            item
            for item in comments
            if _has_term(item.body, competitor)
        ]
        positive = sum(
            1
            for item in matching
            if any(_has_term(_normalized_text(item.body), term) for term in POSITIVE_TERMS)
        )
        negative = sum(
            1
            for item in matching
            if any(_has_term(_normalized_text(item.body), term) for term in NEGATIVE_TERMS)
        )
        if positive > negative:
            sentiment = "mostly positive"
        elif negative > positive:
            sentiment = "mostly negative"
        else:
            sentiment = "mixed or neutral"
        evidence = tuple(
            Evidence(
                quote=_excerpt(item.body),
                thread_title=item.thread_title,
                thread_url=item.thread_url,
                score=item.score,
                matched_terms=(competitor,),
            )
            for item in sorted(matching, key=lambda value: value.score, reverse=True)[:2]
        )
        findings.append(
            CompetitorFinding(
                name=competitor,
                mentions=len(matching),
                sentiment=sentiment,
                evidence=evidence,
            )
        )
    return tuple(sorted(findings, key=lambda item: item.mentions, reverse=True))


def analyze(dataset: ResearchDataset) -> Analysis:
    return Analysis(
        dataset=dataset,
        pains=_evidence_for(dataset.comments, CATEGORY_TERMS["pains"]),
        triggers=_evidence_for(dataset.comments, CATEGORY_TERMS["triggers"]),
        objections=_evidence_for(dataset.comments, CATEGORY_TERMS["objections"]),
        language=_customer_language(dataset.comments),
        competitors=_competitor_findings(dataset.comments, dataset.competitors),
    )


def _render_evidence(items: tuple[Evidence, ...]) -> list[str]:
    if not items:
        return ["No repeated signal cleared the evidence threshold in this sample."]
    return [
        f'- "{_escape_markdown(item.quote)}" ([source thread]({item.thread_url}))'
        for item in items
    ]


def _deterministic_summary(analysis: Analysis) -> str:
    pain = analysis.pains[0].matched_terms[0] if analysis.pains else "an unresolved workflow"
    objection = analysis.objections[0].matched_terms[0] if analysis.objections else "switching risk"
    return (
        f"The strongest pattern is {pain}. Buyers begin evaluating alternatives when the issue "
        f"creates a visible operating burden, but {objection} can stall the decision. The evidence "
        "below keeps each claim traceable to its source thread."
    )


def render_report(analysis: Analysis, *, llm_summary: str | None = None) -> str:
    dataset = analysis.dataset
    lines = [
        f"# Buyer Research Report: {_escape_markdown(dataset.niche)}",
        "",
        (
            f"Collected {dataset.collected_on} from "
            f"{', '.join('r/' + _escape_markdown(item) for item in dataset.subreddits)}. "
            f"Analyzed {len(dataset.posts)} posts and {len(dataset.comments)} comments."
        ),
        "",
        "## Executive summary",
        "",
        _escape_markdown(" ".join(llm_summary.split()))
        if llm_summary
        else _deterministic_summary(analysis),
        "",
        "## Pains",
        "",
        *_render_evidence(analysis.pains),
        "",
        "## Buying triggers",
        "",
        *_render_evidence(analysis.triggers),
        "",
        "## Objections",
        "",
        *_render_evidence(analysis.objections),
        "",
        "## Words customers actually use",
        "",
    ]
    if analysis.language:
        lines.extend(f"- `{phrase}`: {count} uses" for phrase, count in analysis.language)
    else:
        lines.append("No phrase appeared in at least two separate comments.")

    lines.extend(["", "## Competitor mentions", ""])
    if not analysis.competitors:
        lines.append("No competitors were configured for tracking.")
    else:
        lines.extend(
            [
                "| Competitor | Mentions | Direction | Best evidence |",
                "| --- | ---: | --- | --- |",
            ]
        )
        for finding in analysis.competitors:
            evidence = (
                f'"{_escape_markdown(finding.evidence[0].quote)}" '
                f"([source]({finding.evidence[0].thread_url}))"
                if finding.evidence
                else "No mention found"
            )
            lines.append(
                f"| {_escape_markdown(finding.name)} | {finding.mentions} | "
                f"{finding.sentiment} | {evidence} |"
            )

    lines.extend(
        [
            "",
            "## Method notes",
            "",
            "- Evidence is ranked by comment score plus matched buyer-signal terms.",
            "- Language requires the same phrase to appear in at least two separate comments.",
            "- Competitor direction is a keyword heuristic, not a statistical sentiment model.",
            "- Quotes are short excerpts and link back to the discussion context.",
            "",
        ]
    )
    return "\n".join(lines)
