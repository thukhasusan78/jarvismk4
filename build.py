#!/usr/bin/env python3
"""Scan notes/*.md and write viewer/graph-data.js. Standard library only."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NOTES = ROOT / "notes"
OUT = ROOT / "viewer" / "graph-data.js"
WIKI_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
EXCERPT_LEN = 700


def iter_note_files() -> list[Path]:
    return sorted(path for path in NOTES.rglob("*.md") if path.is_file())


def collect_graph() -> dict:
    files = iter_note_files()
    nodes = []
    bodies = []
    for index, path in enumerate(files):
        text = path.read_text(encoding="utf-8")
        bodies.append(text)
        nodes.append(
            {
                "id": index,
                "label": path.stem,
                "group": path.parent.name,
                "excerpt": text[:EXCERPT_LEN],
                "path": path.relative_to(ROOT).as_posix(),
            }
        )

    title_to_id = {node["label"].casefold(): node["id"] for node in nodes}
    seen: set[tuple[int, int]] = set()
    links = []

    def add_link(src: int, dst: int) -> None:
        if src == dst:
            return
        pair = (src, dst) if src < dst else (dst, src)
        if pair in seen:
            return
        seen.add(pair)
        links.append({"source": pair[0], "target": pair[1]})

    for src, body in enumerate(bodies):
        for wiki in WIKI_RE.findall(body):
            dst = title_to_id.get(wiki.strip().casefold())
            if dst is not None:
                add_link(src, dst)
        for node in nodes:
            if node["id"] == src:
                continue
            if node["label"] in body:
                add_link(src, node["id"])

    return {"nodes": nodes, "links": links}


def write_graph(graph: dict | None = None) -> Path:
    data = graph if graph is not None else collect_graph()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "const GRAPH = " + json.dumps(data, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    return OUT


def main() -> None:
    graph = collect_graph()
    dest = write_graph(graph)
    print(f"Wrote {len(graph['nodes'])} nodes and {len(graph['links'])} links to {dest}")


if __name__ == "__main__":
    main()
