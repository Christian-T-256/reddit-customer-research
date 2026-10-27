import json
from pathlib import Path

from reddit_customer_research.analysis import MAX_QUOTE_WORDS, analyze, render_report
from reddit_customer_research.models import Comment, ResearchDataset


ROOT = Path(__file__).resolve().parents[1]


def load_fixture() -> ResearchDataset:
    payload = json.loads(
        (ROOT / "fixtures" / "fictional_reddit_research.json").read_text(encoding="utf-8")
    )
    return ResearchDataset.from_dict(payload)


def test_report_contains_required_buyer_research_sections_and_sources():
    report = render_report(analyze(load_fixture()))

    assert "## Pains" in report
    assert "## Buying triggers" in report
    assert "## Objections" in report
    assert "## Words customers actually use" in report
    assert "## Competitor mentions" in report
    assert "https://example.com/r/FictionalDentalOps/comments/demo101" in report
    assert "Northwind Scheduler" in report


def test_customer_language_requires_repetition():
    findings = analyze(load_fixture())
    phrases = {phrase for phrase, _ in findings.language}

    assert any("switching feels risky" in phrase for phrase in phrases)
    assert all(count >= 2 for _, count in findings.language)


def test_quotes_are_bounded_excerpts():
    findings = analyze(load_fixture())
    all_evidence = findings.pains + findings.triggers + findings.objections

    assert all(len(item.quote.split()) <= MAX_QUOTE_WORDS for item in all_evidence)


def test_untrusted_markdown_is_escaped_in_report():
    dataset = load_fixture()
    unsafe = Comment(
        body="A manual <script>problem</script> with [bad](https://unsafe.example).",
        score=100,
        thread_title="Unsafe formatting",
        thread_url="https://example.com/thread",
        subreddit="FictionalDentalOps",
    )
    findings = analyze(
        ResearchDataset(
            niche="**unsafe niche**",
            collected_on=dataset.collected_on,
            subreddits=dataset.subreddits,
            competitors=dataset.competitors,
            posts=dataset.posts,
            comments=dataset.comments + (unsafe,),
        )
    )

    report = render_report(findings)

    assert "# Buyer Research Report: \\*\\*unsafe niche\\*\\*" in report
    assert "&lt;script&gt;" in report
    assert "\\[bad\\]" in report
