from dataclasses import dataclass

import pytest

from reddit_customer_research.models import Post
from reddit_customer_research.reddit import RedditClient, deduplicate_posts, validate_subreddit


@dataclass
class FakeResponse:
    status_code: int
    payload: object
    headers: dict[str, str] | None = None

    def __post_init__(self):
        self.headers = self.headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self):
        self.current = 0.0
        self.sleeps = []

    def now(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += seconds


def test_client_waits_between_requests():
    clock = FakeClock()
    session = FakeSession([FakeResponse(200, {}), FakeResponse(200, {})])
    client = RedditClient(
        user_agent="research-test/1.0",
        session=session,
        clock=clock.now,
        sleeper=clock.sleep,
    )

    client.get_json("/r/test/hot.json", {})
    client.get_json("/r/test/top.json", {})

    assert clock.sleeps == [pytest.approx(1.1)]


def test_client_retries_429_with_long_backoff():
    clock = FakeClock()
    session = FakeSession(
        [
            FakeResponse(429, {}, {"Retry-After": "12"}),
            FakeResponse(200, {"data": {"children": []}}),
        ]
    )
    client = RedditClient(
        user_agent="research-test/1.0",
        session=session,
        clock=clock.now,
        sleeper=clock.sleep,
    )

    result = client.get_json("/r/test/hot.json", {})

    assert result == {"data": {"children": []}}
    assert clock.sleeps[0] == pytest.approx(30.0)
    assert len(session.calls) == 2


def test_client_retries_network_errors():
    import requests

    clock = FakeClock()
    session = FakeSession(
        [requests.ConnectionError("offline"), FakeResponse(200, {"ok": True})]
    )
    client = RedditClient(
        user_agent="research-test/1.0",
        session=session,
        clock=clock.now,
        sleeper=clock.sleep,
    )

    assert client.get_json("/r/test/hot.json", {}) == {"ok": True}
    assert len(session.calls) == 2
    assert clock.sleeps[0] == pytest.approx(1.0)


def test_subreddit_validation_blocks_endpoint_injection():
    with pytest.raises(ValueError):
        validate_subreddit("../search?q=anything")


def test_client_will_not_allow_unsafe_pacing_settings():
    with pytest.raises(ValueError):
        RedditClient(user_agent="research-test/1.0", request_delay=0.5)
    with pytest.raises(ValueError):
        RedditClient(user_agent="research-test/1.0", rate_limit_backoff=5)


def test_deduplication_keeps_highest_engagement_counts():
    first = Post("abc", "Title", "", "test", 4, 8, "https://www.reddit.com/r/test/comments/abc/x")
    second = Post("abc", "Title", "Body", "test", 7, 6, "https://www.reddit.com/r/test/comments/abc/x")

    result = deduplicate_posts([first, second])

    assert len(result) == 1
    assert result[0].score == 7
    assert result[0].num_comments == 8
    assert result[0].selftext == "Body"
