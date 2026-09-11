"""Curriculum engine — the Quarto half.

A port of the blog's `hooks/curriculum.py`. `series/*.yml` stays the single
source of truth for which modules exist, what order they read in, what level
they are and what each one assumes. From that this module generates, on every
render:

  * `_sidebar.yml` — the whole site nav, picked up via `metadata-files`
  * `series/index.qmd` and one syllabus page per series
  * a header strip and previous/next links on every module page
  * the series grid and recent list on the homepage

Three things the MkDocs hook did are gone, because Quarto config does them:
`_collapse_notebook_code` (now `code-fold: true`) and `_reading_minutes` (now
Quarto listings' `reading-time`), and the `@@REF:` token dance — MkDocs would
not rewrite an href inside raw HTML, so generated links had to be resolved
against the built file tree. The site is served from the domain root, so a
root-relative `/blogs/foo.html` is simply correct from any page.

Where MkDocs could hand generated content back from an event, Quarto expands
`{{< include >}}` *before* the pre-render step, so generated fragments cannot be
included. Per-page furniture is therefore written into the post itself between
comment markers, the way the MkDocs hook already did the homepage and the way
`tools/prerender.py` does mermaid classDefs. Re-running is a no-op.

It fails the render on a syllabus that points at a missing file or a
prerequisite id that nothing defines, which is what keeps the graph honest.
"""

from __future__ import annotations

import glob
import html
import json
import os
import re
import sys
from datetime import datetime

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ACADEMIC_URL = "https://kamingfung.github.io/academic"
# Pages that are deliberately not modules.
UNCLAIMED_OK = {"blogs/index.md", "blogs/templates-showcase.md"}
LEVELS = ("introductory", "intermediate", "advanced")
LEVEL_NAME = {
    "introductory": "Introductory",
    "intermediate": "Intermediate",
    "advanced": "Advanced",
}


class CurriculumError(Exception):
    """Fails the render, the way mkdocs' PluginError did."""


def warn(msg: str) -> None:
    print(f"curriculum: warning: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# paths
#
# `series/*.yml` names posts by their MkDocs source path, `blogs/foo.md`. In
# this repo that post is `blogs/foo.qmd` (notebooks keep their extension), and
# both spellings exist while the content port is in flight. `key` is the
# `.md` spelling, used to talk to the yml; `source` is what is on disk; `href`
# is where it lands.
# --------------------------------------------------------------------------
def source_of(key: str) -> str | None:
    """The file on disk that `key` names, or None if nothing is there."""
    candidates = [key[:-3] + ".qmd", key] if key.endswith(".md") else [key]
    for rel in candidates:
        if os.path.exists(os.path.join(ROOT, rel)):
            return rel
    return None


def href(key: str) -> str:
    """Root-relative URL of the page `key` renders to."""
    return "/" + os.path.splitext(key)[0] + ".html"


# --------------------------------------------------------------------------
# loading and validation
# --------------------------------------------------------------------------
def load_series() -> tuple[list, list, list]:
    series, seminars, moved = [], [], []
    for path in sorted(glob.glob(os.path.join(ROOT, "series", "*.yml"))):
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if "parts" in data:
            series.append(data)
        seminars.extend(data.get("seminars") or [])
        moved.extend(data.get("move_to_academic") or [])
    series.sort(key=lambda s: s.get("order", 999))
    return series, seminars, moved


def index_modules(series: list) -> tuple[dict, dict]:
    """id -> module, and source key -> module."""
    by_id, by_path = {}, {}
    for s in series:
        for pnum, part in enumerate(s["parts"], 1):
            for pos, mod in enumerate(part["modules"]):
                mod["series"] = s
                mod["part"] = part
                mod["part_number"] = pnum
                mod["position"] = pos
                if mod["id"] in by_id:
                    raise CurriculumError(f"duplicate module id {mod['id']!r}")
                by_id[mod["id"]] = mod
                if mod.get("path"):
                    if mod["path"] in by_path:
                        raise CurriculumError(
                            f"{mod['path']} is claimed by two modules"
                        )
                    by_path[mod["path"]] = mod
    return by_id, by_path


def validate(seminars, moved, by_id, by_path) -> None:
    problems = []

    for key, owner in list(by_path.items()) + [
        (x["path"], x) for x in seminars + moved
    ]:
        if source_of(key) is None:
            problems.append(f"missing file: {key} (claimed by {owner.get('title')!r})")

    for mod in by_id.values():
        if mod.get("split_from"):
            # No module uses it today. Fail loudly rather than silently drop the
            # module out of the nav if one ever does.
            problems.append(
                f"{mod['id']} uses split_from, which this port does not implement"
            )
        for req in mod.get("requires") or []:
            if req not in by_id:
                problems.append(f"{mod['id']} requires unknown id {req!r}")

    claimed = set(by_path) | {x["path"] for x in seminars + moved}
    on_disk = set()
    for pattern in ("blogs/**/*.md", "blogs/**/*.qmd", "blogs/**/*.ipynb"):
        for p in glob.glob(os.path.join(ROOT, pattern), recursive=True):
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            # Both spellings of a post are the same post while the content
            # port is in flight; the yml only ever knows the .md one.
            on_disk.add(rel[:-4] + ".md" if rel.endswith(".qmd") else rel)
    for key in sorted(on_disk - claimed - UNCLAIMED_OK):
        warn(f"{key} is in no series and no seminar")

    if problems:
        raise CurriculumError("\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------
# post metadata
# --------------------------------------------------------------------------
_FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _meta(key: str) -> dict:
    """A post's frontmatter, or a notebook's metadata."""
    rel = source_of(key)
    if rel is None:
        return {}
    path = os.path.join(ROOT, rel)
    try:
        if rel.endswith(".ipynb"):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh).get("metadata") or {}
        with open(path, encoding="utf-8") as fh:
            m = _FM.match(fh.read())
        return (yaml.safe_load(m.group(1)) or {}) if m else {}
    except Exception:  # a broken post is reported by the render itself
        return {}


def post_date(key: str) -> str:
    date = _meta(key).get("date")
    if date:
        return str(date)[:10]
    rel = source_of(key)
    try:
        stamp = os.path.getmtime(os.path.join(ROOT, rel))
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
    except Exception:
        return "1970-01-01"


def description(key: str) -> str:
    return str(_meta(key).get("description") or "")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def series_written(s) -> list:
    return [m for p in s["parts"] for m in p["modules"] if m.get("path")]


def series_modules(s) -> list:
    return [m for p in s["parts"] for m in p["modules"]]


def level_range(s) -> str:
    present = sorted({m["level"] for m in series_modules(s)}, key=LEVELS.index)
    if len(present) == 1:
        return LEVEL_NAME[present[0]]
    return f"{LEVEL_NAME[present[0]]} to {LEVEL_NAME[present[-1]].lower()}"


def syllabus_key(s) -> str:
    return f"series/{s['slug']}/index.qmd"


def badge(level: str) -> str:
    return f'<span class="lvl lvl-{level}">{LEVEL_NAME[level]}</span>'


# --------------------------------------------------------------------------
# nav -> _sidebar.yml
# --------------------------------------------------------------------------
def home_key() -> str:
    return "index.qmd" if os.path.exists(os.path.join(ROOT, "index.qmd")) else "index.md"


def build_sidebar(series, seminars) -> list:
    """The same tree `_build_nav` gave mkdocs, in Quarto's sidebar shape."""
    series_nav = [{"text": "Series", "href": "series/index.qmd"}]
    for s in series:
        items = [{"text": s["title"], "href": syllabus_key(s)}]
        entries = lambda mods: [  # noqa: E731
            {"text": m["title"], "href": source_of(m["path"])}
            for m in mods
            if m.get("path")
        ]

        if len(series_written(s)) > 6:
            for pnum, part in enumerate(s["parts"], 1):
                sub = entries(part["modules"])
                if sub:
                    items.append(
                        {"section": f"Part {pnum} · {part['title']}", "contents": sub}
                    )
        else:
            items.extend(entries(series_modules(s)))
        if len(items) > 1:
            series_nav.append({"section": s["title"], "contents": items})

    contents = [
        {"text": "Home", "href": home_key()},
        {"section": "Series", "contents": series_nav},
    ]
    if seminars:
        contents.append(
            {
                "section": "Seminars",
                "contents": [
                    {"text": x["title"], "href": source_of(x["path"])}
                    for x in seminars
                ],
            }
        )
    contents.append({"text": "Academic", "href": ACADEMIC_URL})
    return contents


# --------------------------------------------------------------------------
# generated pages
# --------------------------------------------------------------------------
def catalogue_qmd(series, seminars) -> str:
    rows = []
    for s in series:
        mods, written = series_modules(s), series_written(s)
        rows.append(
            f'<a class="cat-item" href="{href(syllabus_key(s))}">'
            f'<span class="cat-title">{html.escape(s["title"])}</span>'
            f'<span class="cat-about">{html.escape(s["about"])}</span>'
            f'<span class="cat-facts">{level_range(s)}'
            f" · {len(s['parts'])} parts"
            f" · {len(written)} of {len(mods)} written</span></a>"
        )
    seminar_link = (
        f'[Seminars]({href(seminars[0]["path"])})' if seminars else "Seminars"
    )
    return (
        "---\ntitle: Series\n"
        "description: The series these notes are organised into.\n---\n\n"
        "Each series is a set of parts. A part is about an hour of reading and runs to "
        "four or five modules. A module is one page, five to fifteen minutes. Nothing "
        "here has to be read in order, but the order is there if you want it.\n\n"
        '<div class="cat">' + "".join(rows) + "</div>\n\n"
        f"Posts that belong to no series are under {seminar_link}. Modules that are "
        "planned but not written are listed on each series page, greyed out.\n"
    )


def syllabus_qmd(s) -> str:
    mods, written = series_modules(s), series_written(s)
    out = [
        "---",
        f"title: {json.dumps(s['title'], ensure_ascii=False)}",
        f"description: {json.dumps(s['about'], ensure_ascii=False)}",
        "---",
        "",
        # No repeat of `about` in the body: Quarto prints the description under
        # the title, which MkDocs Material did not.
        '<dl class="syl-meta">',
        f"<div><dt>Level</dt><dd>{level_range(s)}</dd></div>",
        f"<div><dt>Parts</dt><dd>{len(s['parts'])}</dd></div>",
        f"<div><dt>Modules</dt><dd>{len(written)} of {len(mods)} written</dd></div>",
        f'<div class="wide"><dt>Assumes</dt><dd>{html.escape(s["assumes"])}</dd></div>',
        "</dl>",
        "",
    ]
    for pnum, part in enumerate(s["parts"], 1):
        out += [f"## Part {pnum} · {part['title']}", "", '<ul class="syl">']
        for m in part["modules"]:
            if m.get("path"):
                out.append(
                    f'<li class="w"><a href="{href(m["path"])}">'
                    f'{html.escape(m["title"])}</a>'
                    f'<span class="m">{LEVEL_NAME[m["level"]]}</span></li>'
                )
            else:
                out.append(
                    f'<li class="g">{html.escape(m["title"])}'
                    f'<span class="m">{LEVEL_NAME[m["level"]]} · not written</span></li>'
                )
        out += ["</ul>", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------
# homepage blocks
# --------------------------------------------------------------------------
def recent_html(by_path, seminars, count=6) -> str:
    items = []
    for key, mod in by_path.items():
        items.append(
            (post_date(key), key, mod["title"], mod["series"]["title"],
             syllabus_key(mod["series"]))
        )
    for sem in seminars:
        items.append((post_date(sem["path"]), sem["path"], sem["title"], "Seminar", None))
    items.sort(reverse=True)

    lines = ["## Recently written", "", '<ul class="recent">']
    for date, key, title, series_title, series_key in items[:count]:
        try:
            shown = datetime.strptime(date, "%Y-%m-%d").strftime("%b %Y")
        except Exception:
            shown = date
        crumb = (
            f'<a href="{href(series_key)}">{html.escape(series_title)}</a>'
            if series_key
            else html.escape(series_title)
        )
        desc = description(key)
        if len(desc) > 150:
            desc = desc[:147].rsplit(" ", 1)[0] + "…"
        lines.append(
            f'<li><a class="r-t" href="{href(key)}">{html.escape(title)}</a>'
            f'<span class="r-m">{crumb} · {shown}</span>'
            f'<span class="r-d">{html.escape(desc)}</span></li>'
        )
    lines.append("</ul>")
    return "\n".join(lines)


def catalogue_cards_html(series) -> str:
    rows = []
    for s in series:
        written = len(series_written(s))
        if not written:
            continue
        rows.append(
            f'<a class="cat-item" href="{href(syllabus_key(s))}">'
            f'<span class="cat-title">{html.escape(s["title"])}</span>'
            f'<span class="cat-about">{html.escape(s["about"])}</span>'
            f'<span class="cat-facts">{level_range(s)} · {len(s["parts"])} parts · '
            f"{written} written</span></a>"
        )
    return (
        "## Series\n\n"
        '<div class="cat">' + "".join(rows) + "</div>\n\n"
        "[Every series, including what is not written yet](/series/index.html)"
    )


# --------------------------------------------------------------------------
# module header and footer
# --------------------------------------------------------------------------
def module_header(mod, by_id) -> str:
    s = mod["series"]
    bits = []
    for req in mod.get("requires") or []:
        target = by_id[req]
        if target.get("path"):
            bits.append(
                f'<a href="{href(target["path"])}">{html.escape(target["title"])}</a>'
            )
        else:
            bits.append(
                f'<span class="unwritten" title="not written yet">'
                f"{html.escape(target['title'])}</span>"
            )
    if bits:
        joined = bits[0] if len(bits) == 1 else ", ".join(bits[:-1]) + " and " + bits[-1]
        assumes = f'<p class="mh-assumes">Assumes {joined}.</p>'
    else:
        assumes = f'<p class="mh-assumes">{html.escape(s["assumes"])}</p>'

    return (
        '<div class="mh">'
        f'<p class="mh-crumb"><a href="{href(syllabus_key(s))}">'
        f'{html.escape(s["title"])}</a><span class="sep">·</span>'
        f'Part {mod["part_number"]} · {html.escape(mod["part"]["title"])}</p>'
        f'<p class="mh-facts">{badge(mod["level"])}</p>'
        f"{assumes}"
        "</div>"
    )


def module_footer(mod) -> str:
    s = mod["series"]
    written = series_written(s)
    i = written.index(mod)
    prv = written[i - 1] if i > 0 else None
    nxt = written[i + 1] if i + 1 < len(written) else None
    parts = []
    if prv:
        parts.append(
            f'<a class="mf-prev" href="{href(prv["path"])}">'
            f"<span>Previous</span>{html.escape(prv['title'])}</a>"
        )
    parts.append(
        f'<a class="mf-up" href="{href(syllabus_key(s))}"><span>Series</span>'
        f"{html.escape(s['title'])}</a>"
    )
    if nxt:
        parts.append(
            f'<a class="mf-next" href="{href(nxt["path"])}">'
            f"<span>Next</span>{html.escape(nxt['title'])}</a>"
        )
    return '<div class="mf">' + "".join(parts) + "</div>"


# --------------------------------------------------------------------------
# writing: everything below is idempotent, a second render changes nothing
# --------------------------------------------------------------------------
def write_if_changed(rel: str, text: str) -> bool:
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def splice(text: str, name: str, block: str, fallback) -> str:
    """Replace the `name` block, or place it with `fallback(text, wrapped)`."""
    start, end = f"<!-- {name}_START -->", f"<!-- {name}_END -->"
    wrapped = f"{start}\n{block}\n{end}"
    # `\n*` on the tail so a replacement always leaves exactly one blank line
    # behind it, whatever the previous render left.
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n*", re.S)
    if pattern.search(text):
        return pattern.sub(lambda _m: wrapped + "\n\n", text, count=1)
    return fallback(text, wrapped)


def _after_frontmatter(text: str, block: str) -> str:
    m = _FM.match(text)
    cut = m.end() if m else 0
    # The trailing blank line matters: without it pandoc runs the raw HTML
    # block on into whatever the post opens with, usually a ::: callout.
    return text[:cut] + "\n" + block + "\n\n" + text[cut:].lstrip("\n")


def _at_end(text: str, block: str) -> str:
    return text.rstrip("\n") + "\n\n" + block + "\n"


def inject_qmd(rel: str, header: str, footer: str) -> bool:
    path = os.path.join(ROOT, rel)
    with open(path, encoding="utf-8") as fh:
        before = fh.read()
    after = splice(before, "CURRICULUM_HEADER", header, _after_frontmatter)
    after = splice(after, "CURRICULUM_FOOTER", footer, _at_end)
    if after == before:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(after)
    return True


def inject_ipynb(rel: str, header: str, footer: str) -> bool:
    """Same markers, carried in markdown cells of their own."""
    path = os.path.join(ROOT, rel)
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    nb = json.loads(raw)
    cells = [
        c
        for c in nb["cells"]
        if "CURRICULUM_HEADER_START" not in "".join(c.get("source") or [])
        and "CURRICULUM_FOOTER_START" not in "".join(c.get("source") or [])
    ]

    def cell(name, block):
        return {
            "cell_type": "markdown",
            "metadata": {},
            "source": (f"<!-- {name}_START -->\n{block}\n<!-- {name}_END -->").splitlines(
                keepends=True
            ),
        }

    # After the opening cell, which carries the H1 Quarto titles the page from.
    cells.insert(min(1, len(cells)), cell("CURRICULUM_HEADER", header))
    cells.append(cell("CURRICULUM_FOOTER", footer))
    nb["cells"] = cells
    text = json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
    if text == raw:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


# The furniture the MkDocs site styles from docs/stylesheets/curriculum.css.
# Material's --md-* tokens are not defined here, so this is the same ruleset
# rewritten against Bootstrap's and the generated --ink-* palette tokens.
CSS = """/* Generated by tools/curriculum.py — do not edit. */
:root { --rule: var(--bs-border-color); --dim: var(--bs-secondary-color); }
.lvl { font-size: .62rem; letter-spacing: .06em; text-transform: uppercase;
  font-weight: 600; padding: .1em .5em; border-radius: 2px;
  border: 1px solid currentcolor; white-space: nowrap; }
.lvl-introductory { color: var(--ink-sage); }
.lvl-intermediate { color: var(--ink-ochre); }
.lvl-advanced     { color: var(--ink-rust); }

.mh { margin: -.4rem 0 2rem; padding: 0 0 .9rem;
  border-bottom: 1px solid var(--rule); font-size: .74rem; }
.mh p { margin: 0 0 .35rem; }
.mh-crumb { color: var(--dim); }
.mh-crumb .sep { margin: 0 .45em; opacity: .5; }
.mh-crumb a { color: inherit; text-decoration: none;
  border-bottom: 1px solid var(--rule); }
.mh-facts { display: flex; align-items: center; gap: .7rem; }
.mh-assumes { color: var(--dim); max-width: 62ch; }
.unwritten { color: var(--dim); border-bottom: 1px dotted currentcolor;
  cursor: help; }

.mf { display: flex; flex-wrap: wrap; gap: .75rem; margin: 3rem 0 0;
  padding-top: 1rem; border-top: 1px solid var(--rule); }
.mf a { flex: 1 1 14rem; display: block; padding: .7rem .9rem;
  border: 1px solid var(--rule); border-radius: 2px; text-decoration: none;
  color: var(--bs-body-color); font-size: .8rem; line-height: 1.35; }
.mf a span { display: block; font-size: .62rem; letter-spacing: .08em;
  text-transform: uppercase; color: var(--dim); margin-bottom: .2rem; }
.mf-up { flex: 0 1 12rem; text-align: center; }
.mf-next { text-align: right; }

.syl-meta { display: grid; grid-template-columns: repeat(3, auto) 1fr;
  gap: .3rem 2rem; margin: 0 0 2.5rem; padding-bottom: 1rem;
  border-bottom: 1px solid var(--rule); font-size: .75rem; }
.syl-meta .wide { grid-column: 1 / -1; }
.syl-meta dt { font-size: .6rem; letter-spacing: .08em;
  text-transform: uppercase; color: var(--dim); margin-bottom: .1rem; }
.syl-meta dd { margin: 0; }
@media (max-width: 44em) { .syl-meta { grid-template-columns: 1fr 1fr; } }

ul.syl { list-style: none; margin: 0 0 2rem; padding: 0; }
ul.syl li { display: flex; flex-wrap: wrap; align-items: baseline;
  gap: .3rem 1rem; padding: .42rem 0;
  border-bottom: 1px dotted var(--rule); font-size: .8rem; }
ul.syl li:last-child { border-bottom: 0; }
ul.syl .m { margin-left: auto; font-size: .68rem; color: var(--dim);
  white-space: nowrap; }
ul.syl li.g { color: var(--dim); }

.cat { display: grid; grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
  gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  margin: 1.5rem 0 2rem; }
a.cat-item { display: flex; flex-direction: column; gap: .35rem;
  padding: 1rem 1.1rem 1.1rem; background: var(--bs-body-bg);
  text-decoration: none; color: var(--bs-body-color); }
.cat-title { font-weight: 600; font-size: .9rem; line-height: 1.25; }
.cat-about { font-size: .75rem; color: var(--dim); line-height: 1.45; }
.cat-facts { margin-top: auto; padding-top: .4rem; font-size: .65rem;
  letter-spacing: .03em; color: var(--dim); }

ul.recent { list-style: none; margin: 1rem 0 2.5rem; padding: 0; }
ul.recent li { padding: .7rem 0; border-bottom: 1px dotted var(--rule); }
a.r-t { display: block; font-weight: 500; font-size: .88rem; line-height: 1.3; }
.r-m { display: block; margin: .15rem 0 .25rem; font-size: .66rem;
  letter-spacing: .03em; color: var(--dim); }
.r-d { display: block; font-size: .75rem; color: var(--dim);
  line-height: 1.5; max-width: 68ch; }
"""


def run() -> int:
    try:
        series, seminars, moved = load_series()
        by_id, by_path = index_modules(series)
        validate(seminars, moved, by_id, by_path)
    except CurriculumError as exc:
        print(f"curriculum: {exc}", file=sys.stderr)
        return 1

    write_if_changed("curriculum.css", CSS)
    # `metadata-files` merges this whole document into the project metadata, so
    # the generated stylesheet rides along with the nav rather than needing a
    # hand-edit of _quarto.yml.
    write_if_changed(
        "_sidebar.yml",
        "# Generated by tools/curriculum.py from series/*.yml — do not edit.\n"
        + yaml.safe_dump(
            {
                "website": {"sidebar": {"contents": build_sidebar(series, seminars)}},
                "format": {"html": {"css": ["curriculum.css"]}},
            },
            sort_keys=False,
            allow_unicode=True,
        ),
    )

    write_if_changed("series/index.qmd", catalogue_qmd(series, seminars))
    for s in series:
        write_if_changed(syllabus_key(s), syllabus_qmd(s))

    home = home_key()
    with open(os.path.join(ROOT, home), encoding="utf-8") as fh:
        text = fh.read()
    for name, block in (
        ("SERIES_CATALOGUE", catalogue_cards_html(series)),
        ("RECENT_POSTS", recent_html(by_path, seminars)),
    ):
        text = splice(text, name, block, _at_end)
    write_if_changed(home, text)

    touched, skipped = 0, 0
    for key, mod in by_path.items():
        rel = source_of(key)
        if rel.endswith(".md"):  # not ported to .qmd yet; leave the source alone
            skipped += 1
            continue
        header, footer = module_header(mod, by_id), module_footer(mod)
        edit = inject_ipynb if rel.endswith(".ipynb") else inject_qmd
        touched += bool(edit(rel, header, footer))

    print(
        f"curriculum: {len(series)} series, {len(by_id)} modules, "
        f"{len(by_path)} written; {touched} page(s) updated"
        + (f", {skipped} still .md" if skipped else "")
    )
    return 0


def _selftest() -> None:
    assert href("blogs/foo.md") == "/blogs/foo.html"
    assert href("blogs/a/b.ipynb") == "/blogs/a/b.html"
    assert href("series/x/index.qmd") == "/series/x/index.html"

    doc = "---\ntitle: t\n---\nbody\n"
    once = splice(doc, "CURRICULUM_HEADER", "<div/>", _after_frontmatter)
    assert "<div/>" in once and once.index("<div/>") > once.index("title: t")
    assert "_END -->\n\nbody" in once, once  # a blank line, or pandoc eats `body`
    assert splice(once, "CURRICULUM_HEADER", "<div/>", _after_frontmatter) == once
    twice = splice(once, "CURRICULUM_HEADER", "<p/>", _after_frontmatter)
    assert "<div/>" not in twice and twice.count("<p/>") == 1
    assert splice(twice, "CURRICULUM_FOOTER", "<hr/>", _at_end).endswith(
        "<!-- CURRICULUM_FOOTER_END -->\n"
    )

    series, seminars, _ = load_series()
    by_id, by_path = index_modules(series)
    bar = build_sidebar(series, seminars)
    assert bar[0] == {"text": "Home", "href": home_key()}
    assert bar[-1]["href"] == ACADEMIC_URL
    assert all(i.get("href") for i in bar[1]["contents"][0:1])
    # Every href in the sidebar is a file that exists.
    def walk(items):
        for i in items:
            if "contents" in i:
                yield from walk(i["contents"])
            elif not i["href"].startswith("http"):
                yield i["href"]
    for h in walk(bar):
        assert h.startswith("series/") or os.path.exists(os.path.join(ROOT, h)), h
    print(f"curriculum: selftest ok ({len(by_id)} modules, {len(by_path)} written)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(run())
