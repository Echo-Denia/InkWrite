"""Intermediate representation: a JSON structure holding typed content blocks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Block:
    type: str                       # title | heading | text | math | table | image | list
    text: str = ""
    bbox: list[float] | None = None
    page: int = 0
    level: int = 0                  # heading depth
    numbered: bool = False          # display math with equation number
    runs: list[dict] = field(default_factory=list)


@dataclass
class Page:
    index: int
    size: tuple[int, int]
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Document:
    pages: list[Page]

    def save(self, path: Path) -> None:
        data = {
            "pages": [
                {
                    "index": pg.index,
                    "size": list(pg.size),
                    "blocks": [
                        {
                            "type": b.type,
                            "text": b.text,
                            "bbox": b.bbox,
                            "page": b.page,
                            "level": b.level,
                            "numbered": b.numbered,
                            "runs": b.runs,
                        }
                        for b in pg.blocks
                    ],
                }
                for pg in self.pages
            ]
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    @property
    def blocks(self) -> list[Block]:
        return [b for pg in self.pages for b in pg.blocks]

    @classmethod
    def from_pages(cls, pages: list[Page]) -> "Document":
        return cls(pages)


def page_wrapper(pages: list[Page]) -> Document:
    return Document(pages)
