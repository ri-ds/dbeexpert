"""
faculty.py

The allow list of faculty the assistant is permitted to talk about.

names.csv is the authority because it is what the original app shipped, and it
matches the 20 distinct names encoded in Chunk.source2 in the restored graph
exactly. The graph is consulted only as a fallback when the CSV is missing.
"""

from __future__ import annotations

import csv
import functools
import logging
from pathlib import Path

from .settings import settings

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def faculty_names() -> tuple[str, ...]:
    path = Path(settings.names_csv)
    names: list[str] = []

    if path.exists():
        try:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                for row_number, row in enumerate(csv.reader(handle)):
                    if not row:
                        continue
                    value = (row[0] or "").strip()
                    # Skip the header and any trailing blank rows.
                    if not value or (row_number == 0 and "name" in value.lower()):
                        continue
                    names.append(value)
        except OSError as exc:
            log.warning("Could not read %s: %s", path, exc)
    else:
        log.warning("names.csv not found at %s", path)

    if not names:
        try:
            from .db import faculty_from_graph

            names = faculty_from_graph()
            log.info("Loaded %d faculty names from the graph as a fallback", len(names))
        except Exception as exc:
            log.warning("Could not derive faculty from the graph: %s", exc)

    return tuple(dict.fromkeys(names))


def faculty_list_text() -> str:
    return ", ".join(faculty_names())


def canonicalise(name: str) -> str | None:
    """Map a model supplied name back onto the exact allow list spelling."""
    if not name:
        return None
    target = name.strip().strip("\"'").lower()
    if not target:
        return None

    lookup = {n.lower(): n for n in faculty_names()}
    if target in lookup:
        return lookup[target]

    # Tolerate a surname or a partial name, but only when it is unambiguous.
    matches = [full for lower, full in lookup.items() if target in lower or lower in target]
    if len(matches) == 1:
        return matches[0]
    return None
