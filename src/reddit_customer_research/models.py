"""Small, privacy-conscious data models used by the research pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Post:
    id: str
    title: str
    selftext: str
    subreddit: str
    score: int
    num_comments: int
    permalink: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Post":
        return cls(
            id=str(value["id"]),
            title=str(value["title"]),
            selftext=str(value.get("selftext", "")),
            subreddit=str(value.get("subreddit", "")),
            score=int(value.get("score", 0)),
            num_comments=int(value.get("num_comments", 0)),
            permalink=str(value["permalink"]),
        )


@dataclass(frozen=True)
class Comment:
    body: str
    score: int
    thread_title: str
    thread_url: str
    subreddit: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Comment":
        return cls(
            body=str(value["body"]),
            score=int(value.get("score", 0)),
            thread_title=str(value["thread_title"]),
            thread_url=str(value["thread_url"]),
            subreddit=str(value.get("subreddit", "")),
        )


@dataclass(frozen=True)
class ResearchDataset:
    niche: str
    collected_on: str
    subreddits: tuple[str, ...]
    competitors: tuple[str, ...]
    posts: tuple[Post, ...]
    comments: tuple[Comment, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResearchDataset":
        return cls(
            niche=str(value["niche"]),
            collected_on=str(value["collected_on"]),
            subreddits=tuple(str(item) for item in value.get("subreddits", [])),
            competitors=tuple(str(item) for item in value.get("competitors", [])),
            posts=tuple(Post.from_dict(item) for item in value.get("posts", [])),
            comments=tuple(Comment.from_dict(item) for item in value.get("comments", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "niche": self.niche,
            "collected_on": self.collected_on,
            "subreddits": list(self.subreddits),
            "competitors": list(self.competitors),
            "posts": [asdict(item) for item in self.posts],
            "comments": [asdict(item) for item in self.comments],
        }
