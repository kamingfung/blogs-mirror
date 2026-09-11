"""Give a pre-executed notebook the Quarto frontmatter a listing needs.

Quarto renders .ipynb natively and, with `execute: enabled: false`, never
re-runs a cell — the committed outputs are what ships, exactly as mkdocs-jupyter
does today. The only thing missing is metadata: Quarto wants title/date in
frontmatter, which for a notebook means a raw cell at the top.

    python tools/port_notebook.py <notebook.ipynb> [category ...]
"""

from __future__ import annotations

import json
import re
import sys

import yaml

TLDR = re.compile(r"^> \*\*TL;DR\*\*:?\s*(.+?)(?=\n(?!> )|\Z)", re.M | re.S)


def main() -> None:
    path, categories = sys.argv[1], sys.argv[2:]
    with open(path) as fh:
        nb = json.load(fh)

    if nb["cells"] and nb["cells"][0].get("cell_type") == "raw":
        raise SystemExit(f"{path}: already has a raw frontmatter cell")

    first = nb["cells"][0]
    src = "".join(first["source"])
    title = re.search(r"^# (.+)$", src, re.M)
    if not title:
        raise SystemExit(f"{path}: no H1 to take a title from")

    meta = {
        "title": title.group(1).strip(),
        "description": nb["metadata"].get("description"),
        "date": str(nb["metadata"].get("date", "")),
        "categories": categories,
    }
    meta = {k: v for k, v in meta.items() if v}

    # Drop the H1 (Quarto renders the title) and lift the TL;DR into a callout.
    src = re.sub(r"^# .+?\n+", "", src, count=1, flags=re.M)
    src = TLDR.sub(
        lambda m: '::: {.callout-note appearance="simple" icon=false}\n## TL;DR\n\n'
        + re.sub(r"^> ?", "", m.group(1), flags=re.M).strip()
        + "\n:::",
        src,
        count=1,
    )
    first["source"] = src.splitlines(keepends=True)

    fm = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10_000)
    nb["cells"].insert(
        0,
        {"cell_type": "raw", "metadata": {}, "source": f"---\n{fm}---".splitlines(keepends=True)},
    )

    with open(path, "w") as fh:
        json.dump(nb, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"{path}: frontmatter added ({meta['title']})")


if __name__ == "__main__":
    main()
