"""Shared types for the wiki pages publisher pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PublishedPage:
    """A page written to the output directory."""

    name: str
    path: str
    slug: str
    html_size: int
