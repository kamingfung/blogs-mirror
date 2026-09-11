"""Port a MkDocs Material post to a Quarto .qmd.

Run once per post. Everything MkDocs-specific in these posts is one of five
things, and each maps onto a Quarto equivalent:

  frontmatter   tags -> categories; the rest carries over unchanged
  H1            dropped; Quarto renders the title from frontmatter
  > **TL;DR**   -> a ::: {.callout-note} block
  !!! / ???     -> ::: {.callout-*}, with collapse="true" for the ??? form
  ```mermaid    -> ```{mermaid}, an executable cell Quarto renders itself
  raw HTML/JS   -> wrapped in a ```{=html} block

That last one is the only surprise in the whole port. python-markdown keeps a
block of raw HTML raw until its closing tag; Pandoc ends the block at the first
blank line, and these widgets are full of blank lines and two-space indents. The
tail of each one came back as an escaped <pre><code> listing and every
getElementById in the widget's script returned null. Wrapping the block in
Quarto's raw-passthrough fence fixes it without touching a line of the markup.

Math ($...$, $$...$$) and footnotes ([^1]) are Pandoc-native and need no work.

    python tools/port_from_mkdocs.py <src.md> <dest.qmd>
"""

from __future__ import annotations

import re
import sys

import yaml

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
H1 = re.compile(r"^# .+?\n+", re.M)
TLDR = re.compile(r"^> \*\*TL;DR\*\*:?\s*(.+?)(?=\n(?!> )|\Z)", re.M | re.S)
MERMAID = re.compile(r"^(?P<indent>[ \t]*)```+[ \t]*mermaid[ \t]*$", re.M)
FENCE = re.compile(r"^\s*(```+|~~~+)")
# The block-level tags these posts open a widget with.
HTML_OPEN = re.compile(r"^\s*<(div|script|figure|form|canvas|svg|iframe|table)\b", re.I)
# A relative cross-post link. The .md target is about to stop existing.
MD_LINK = re.compile(r"\]\((?!\w+:)([^)\s]+)\.md(#[^)\s]*)?\)")
# !!! note "Title" / ??? note "Title" / ???+ note "Title" (the last starts open).
ADMON = re.compile(r'^(?P<marker>!!!|\?\?\?\+?)\s+(?P<type>[\w-]+)(?:\s+"(?P<title>[^"]*)")?\s*$')
# Quarto ships five callout styles; anything else in these posts is a note.
CALLOUTS = {"note", "tip", "warning", "important", "caution"}


def convert_admonitions(body: str) -> tuple[str, int]:
    """Rewrite MkDocs admonitions as Quarto callout divs.

    The body of an admonition is everything indented past the marker (blank
    lines included), so the block ends at the first non-blank line that is not.
    Bodies get dedented by their own indent, which puts any nested code fence
    back at column 0 where the later passes expect it."""
    lines = body.splitlines()
    out: list[str] = []
    i, count = 0, 0
    in_fence = False

    while i < len(lines):
        line = lines[i]
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        m = ADMON.match(line) if not in_fence else None
        if not m:
            out.append(line)
            i += 1
            continue

        i += 1
        block = []
        while i < len(lines):
            cur = lines[i]
            if cur.strip() and not cur.startswith((" ", "\t")):
                break
            block.append(cur)
            i += 1
        while block and not block[-1].strip():
            block.pop()

        indent = min(
            (len(c) - len(c.lstrip()) for c in block if c.strip()),
            default=0,
        )
        kind = m.group("type").lower()
        attrs = f".callout-{kind if kind in CALLOUTS else 'note'}"
        if m.group("marker").startswith("???"):
            # ???+ is a collapsible block that starts open; ??? starts closed.
            attrs += ' collapse="false"' if m.group("marker") == "???+" else ' collapse="true"'

        out.append(f"::: {{{attrs}}}")
        if m.group("title"):
            out.append(f"## {m.group('title')}")
            out.append("")
        out.extend(c[indent:] if c.strip() else "" for c in block)
        out.append(":::")
        out.append("")
        count += 1

    return "\n".join(out) + "\n", count


def wrap_raw_html(body: str) -> tuple[str, int]:
    """Fence every top-level raw HTML block as ```{=html}.

    Depth is counted on the root tag only, so nested <div>s inside a widget stay
    part of the same block. Anything inside a code fence is left alone."""
    lines = body.splitlines()
    out: list[str] = []
    i, wrapped = 0, 0
    in_fence = False

    while i < len(lines):
        line = lines[i]
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue

        m = HTML_OPEN.match(line) if not in_fence else None
        if not m:
            out.append(line)
            i += 1
            continue

        tag = m.group(1).lower()
        opener = re.compile(rf"<{tag}\b", re.I)
        closer = re.compile(rf"</{tag}\s*>", re.I)
        block, depth = [], 0
        while i < len(lines):
            cur = lines[i]
            depth += len(opener.findall(cur)) - len(closer.findall(cur))
            block.append(cur)
            i += 1
            if depth <= 0:
                break

        out.append("```{=html}")
        out.extend(block)
        out.append("```")
        out.append("")
        wrapped += 1

    return "\n".join(out) + "\n", wrapped


def port(text: str) -> str:
    m = FM.match(text)
    if not m:
        raise SystemExit("no YAML frontmatter found")
    meta = yaml.safe_load(m.group(1)) or {}
    body = text[m.end() :]

    out = {
        "title": meta.get("title"),
        "description": meta.get("description"),
        "date": meta.get("date"),
        "categories": meta.get("tags", []),
    }
    out = {k: v for k, v in out.items() if v not in (None, [], "")}
    # Everything else carries over untouched, so nothing is dropped silently:
    # `palette:` (tools/prerender.py reads it to pick a mermaid classDef set),
    # `exclude_from_blog:` (blogs/index), and whatever a future post adds.
    for k, v in meta.items():
        if k not in out and k != "tags":
            out[k] = v

    body = H1.sub("", body, count=1)

    def tldr(m: re.Match) -> str:
        inner = re.sub(r"^> ?", "", m.group(1), flags=re.M).strip()
        return f'::: {{.callout-note appearance="simple" icon=false}}\n' f"## TL;DR\n\n{inner}\n:::"

    body = TLDR.sub(tldr, body, count=1)
    body, callouts = convert_admonitions(body)
    if callouts:
        print(f"  {callouts} admonition(s) -> callout div(s)")
    body = MD_LINK.sub(lambda m: f"]({m.group(1)}.qmd{m.group(2) or ''})", body)
    body = MERMAID.sub(lambda m: f"{m.group('indent')}```{{mermaid}}", body)
    body, wrapped = wrap_raw_html(body)
    if wrapped:
        print(f"  {wrapped} raw HTML block(s) fenced as ```{{=html}}")

    fm = yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=10_000)
    return f"---\n{fm}---\n{body.lstrip()}"


def selftest() -> None:
    """`python tools/port_from_mkdocs.py --selftest` — the admonition pass."""
    got, n = convert_admonitions(
        '???+ note "Open"\n'
        "    body\n"
        "\n"
        '??? example "Code"\n'
        "    ```python\n"
        "    x = 1\n"
        "\n"
        "    y = 2\n"
        "    ```\n"
        "\n"
        "plain\n"
        "\n"
        "```text\n"
        '!!! not an admonition, it is inside a fence\n'
        "```\n"
    )
    assert n == 2, n
    assert '::: {.callout-note collapse="false"}\n## Open\n\nbody\n:::' in got, got
    # An unknown type falls back to note, the fenced code keeps its blank line,
    # and the dedent puts the nested fence back at column 0.
    assert '::: {.callout-note collapse="true"}\n## Code\n\n```python\nx = 1\n\ny = 2\n```\n:::' in got, got
    assert "!!! not an admonition" in got and "callout" not in got.split("```text")[1], got
    print("selftest ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        selftest()
        raise SystemExit(0)
    src, dest = sys.argv[1], sys.argv[2]
    with open(src) as fh:
        result = port(fh.read())
    with open(dest, "w") as fh:
        fh.write(result)
    print(f"{src} -> {dest}")
