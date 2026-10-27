"""Reddit public JSON collection with pacing, retries, and data minimization."""

from __future__ import annotations

import re
import time
import warnings
from dataclasses import replace
from datetime import date
from typing import Any, Callable, Iterable

import requests

from .models import Comment, Post, ResearchDataset


BASE_URL = "https://www.reddit.com"
SUBREDDIT_PATTERN = re.compile(r"^[A-Za-z0-9_]{2,21}$")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class RedditCollectionError(RuntimeError):
    """Raised when Reddit cannot be collected safely after bounded retries."""


def validate_subreddit(value: str) -> str:
    cleaned = value.strip().removeprefix("r/")
    if not SUBREDDIT_PATTERN.fullmatch(cleaned):
        raise ValueError(
            f"Invalid subreddit {value!r}. Use 2 to 21 letters, numbers, or underscores."
        )
    return cleaned


def validate_query(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 200 or any(char in cleaned for char in "\r\n\x00"):
        raise ValueError("Search queries must contain 1 to 200 characters on one line.")
    return cleaned


class RedditClient:
    """A narrowly scoped client for Reddit's unauthenticated JSON endpoints."""

    def __init__(
        self,
        *,
        user_agent: str,
        request_delay: float = 1.1,
        timeout: float = 15.0,
        max_attempts: int = 3,
        rate_limit_backoff: float = 30.0,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("A descriptive Reddit user agent is required.")
        if request_delay < 1.0:
            raise ValueError("Request delay cannot be below 1.0 second.")
        if timeout <= 0:
            raise ValueError("Request timeout must be greater than zero.")
        if not 1 <= max_attempts <= 5:
            raise ValueError("Max attempts must be between 1 and 5.")
        if rate_limit_backoff < 30.0:
            raise ValueError("Rate-limit backoff cannot be below 30 seconds.")
        self.user_agent = user_agent
        self.request_delay = request_delay
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.rate_limit_backoff = rate_limit_backoff
        self.session = session or requests.Session()
        self.clock = clock
        self.sleeper = sleeper
        self._last_request_at: float | None = None

    def _pace(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self.request_delay - (self.clock() - self._last_request_at)
        if remaining > 0:
            self.sleeper(remaining)

    def _retry_wait(self, response: requests.Response | Any, attempt: int) -> float:
        if getattr(response, "status_code", 0) == 429:
            header = getattr(response, "headers", {}).get("Retry-After")
            try:
                server_wait = float(header) if header is not None else 0.0
            except ValueError:
                server_wait = 0.0
            return min(max(self.rate_limit_backoff, server_wait), 120.0)
        return min(2.0 ** attempt, 30.0)

    def get_json(self, path: str, params: dict[str, Any]) -> Any:
        if not path.startswith("/") or "//" in path or not path.endswith(".json"):
            raise ValueError("Reddit endpoint must be a relative .json path.")

        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            self._pace()
            try:
                response = self.session.get(
                    BASE_URL + path,
                    headers={"User-Agent": self.user_agent},
                    params=params,
                    timeout=self.timeout,
                )
                self._last_request_at = self.clock()
            except requests.RequestException as exc:
                self._last_request_at = self.clock()
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    self.sleeper(min(2.0 ** attempt, 30.0))
                    continue
                break

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = RedditCollectionError(
                    f"Reddit returned HTTP {response.status_code} for {path}."
                )
                if attempt + 1 < self.max_attempts:
                    self.sleeper(self._retry_wait(response, attempt))
                    continue
                break

            try:
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    self.sleeper(min(2.0 ** attempt, 30.0))
                    continue
                break

        raise RedditCollectionError(
            f"Failed to collect {path} after {self.max_attempts} attempts: {last_error}"
        )

    def scrape_subreddit(
        self,
        subreddit: str,
        *,
        sort: str,
        time_filter: str = "month",
        limit: int = 25,
    ) -> list[Post]:
        subreddit = validate_subreddit(subreddit)
        if sort not in {"hot", "new", "top", "rising"}:
            raise ValueError(f"Unsupported post sort: {sort}")
        bounded_limit = min(max(limit, 1), 100)
        payload = self.get_json(
            f"/r/{subreddit}/{sort}.json",
            {"limit": bounded_limit, "t": time_filter, "raw_json": 1},
        )
        return _parse_listing(payload, fallback_subreddit=subreddit)

    def search(
        self,
        query: str,
        *,
        subreddit: str,
        limit: int = 10,
    ) -> list[Post]:
        subreddit = validate_subreddit(subreddit)
        query = validate_query(query)
        payload = self.get_json(
            f"/r/{subreddit}/search.json",
            {
                "q": query,
                "restrict_sr": "on",
                "sort": "top",
                "t": "year",
                "limit": min(max(limit, 1), 100),
                "raw_json": 1,
            },
        )
        return _parse_listing(payload, fallback_subreddit=subreddit)

    def scrape_comments(self, post: Post, *, limit: int = 30) -> list[Comment]:
        match = re.search(r"/comments/([A-Za-z0-9]+)/", post.permalink + "/")
        if not match:
            raise ValueError(f"Post has an invalid Reddit permalink: {post.permalink}")
        payload = self.get_json(
            f"/comments/{match.group(1)}.json",
            {"limit": min(max(limit, 1), 100), "sort": "top", "depth": 1, "raw_json": 1},
        )
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        children = payload[1].get("data", {}).get("children", [])
        comments: list[Comment] = []
        for child in children:
            if child.get("kind") != "t1":
                continue
            data = child.get("data", {})
            body = str(data.get("body", "")).strip()
            if not body or body in {"[deleted]", "[removed]"}:
                continue
            comments.append(
                Comment(
                    body=body,
                    score=int(data.get("score", 0)),
                    thread_title=post.title,
                    thread_url=post.permalink,
                    subreddit=post.subreddit,
                )
            )
        return comments


def _parse_listing(payload: Any, *, fallback_subreddit: str) -> list[Post]:
    children = payload.get("data", {}).get("children", []) if isinstance(payload, dict) else []
    posts: list[Post] = []
    for child in children:
        data = child.get("data", {})
        post_id = str(data.get("id", "")).strip()
        title = str(data.get("title", "")).strip()
        permalink = str(data.get("permalink", "")).strip()
        if not post_id or not title or not permalink.startswith("/r/"):
            continue
        posts.append(
            Post(
                id=post_id,
                title=title,
                selftext=str(data.get("selftext", "")),
                subreddit=str(data.get("subreddit", fallback_subreddit)),
                score=int(data.get("score", 0)),
                num_comments=int(data.get("num_comments", 0)),
                permalink=BASE_URL + permalink,
            )
        )
    return posts


def deduplicate_posts(posts: Iterable[Post]) -> list[Post]:
    """Keep the richest copy when feeds or searches return the same post."""
    by_id: dict[str, Post] = {}
    for post in posts:
        current = by_id.get(post.id)
        if current is None:
            by_id[post.id] = post
            continue
        by_id[post.id] = replace(
            current,
            score=max(current.score, post.score),
            num_comments=max(current.num_comments, post.num_comments),
            selftext=current.selftext or post.selftext,
        )
    return list(by_id.values())


def collect_research(
    client: RedditClient,
    *,
    niche: str,
    subreddits: Iterable[str],
    keywords: Iterable[str],
    competitors: Iterable[str],
    posts_per_feed: int = 25,
    max_comment_threads: int = 8,
    minimum_comments: int = 5,
) -> ResearchDataset:
    """Collect posts first, then spend the comment budget on debated threads."""
    clean_subreddits = tuple(dict.fromkeys(validate_subreddit(item) for item in subreddits))
    clean_keywords = tuple(dict.fromkeys(validate_query(item) for item in keywords))
    if not niche.strip():
        raise ValueError("Niche cannot be empty.")
    if not clean_subreddits:
        raise ValueError("At least one subreddit is required.")

    discovered: list[Post] = []
    for subreddit in clean_subreddits:
        try:
            discovered.extend(
                client.scrape_subreddit(
                    subreddit, sort="top", time_filter="month", limit=posts_per_feed
                )
            )
        except RedditCollectionError as exc:
            warnings.warn(str(exc), stacklevel=2)
        try:
            discovered.extend(
                client.scrape_subreddit(subreddit, sort="hot", limit=posts_per_feed)
            )
        except RedditCollectionError as exc:
            warnings.warn(str(exc), stacklevel=2)
        for keyword in clean_keywords:
            try:
                discovered.extend(client.search(keyword, subreddit=subreddit, limit=10))
            except RedditCollectionError as exc:
                warnings.warn(str(exc), stacklevel=2)

    posts = deduplicate_posts(discovered)
    if not posts:
        raise RedditCollectionError(
            "No posts were collected. Check subreddit names and try again later."
        )
    candidates = sorted(posts, key=lambda item: (item.num_comments, item.score), reverse=True)
    comments: list[Comment] = []
    threads_collected = 0
    for post in candidates:
        if post.num_comments < minimum_comments or len(comments) >= 500:
            continue
        if threads_collected >= max_comment_threads:
            break
        try:
            comments.extend(client.scrape_comments(post, limit=30))
        except RedditCollectionError as exc:
            warnings.warn(str(exc), stacklevel=2)
        threads_collected += 1

    return ResearchDataset(
        niche=niche.strip(),
        collected_on=date.today().isoformat(),
        subreddits=clean_subreddits,
        competitors=tuple(item.strip() for item in competitors if item.strip()),
        posts=tuple(posts),
        comments=tuple(comments),
    )
