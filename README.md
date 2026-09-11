# blogs-mirror

A full [Quarto](https://quarto.org) mirror of the MkDocs Material site at
**<https://blogs.kamingfung.workers.dev>**. Every post, every series and the
homepage, rendered by a different static site generator and deployed to its own
Cloudflare Worker at **<https://blogs-mirror.kamingfung.workers.dev>**.

The MkDocs site remains canonical. Content is authored there; this repo is a
port of it. If the two disagree, the MkDocs site is right.

## Structure

The source tree mirrors the MkDocs repo exactly, one directory level shallower
(`docs/blogs/foo.md` becomes `blogs/foo.qmd`):

```
series/*.yml             # Source of truth for modules, order, levels, prerequisites
tools/
├── prerender.py         # Generates the palette stylesheets and the sidebar/series pages
├── fallcolor.py         # Copied unchanged from the MkDocs repo
├── palette.yml          # Copied unchanged
├── port_from_mkdocs.py  # .md  -> .qmd
└── port_notebook.py     # .ipynb frontmatter
index.md                 # Homepage
blogs/
├── <name>.qmd           # Prose modules
├── <name>.png           # Their images, sitting next to them
└── <topic>/<name>.ipynb # Code modules (pre-executed; outputs committed)
_quarto.yml              # Site config, annotated with its mkdocs.yml equivalents
custom.scss              # Hand-written styles
wrangler.json            # Cloudflare Workers static-assets config
.github/workflows/deploy-cloudflare.yml
```

That mirroring is deliberate and load-bearing. In the MkDocs repo, images and
data files sit next to the posts that use them and every reference is relative —
`![](output_21_1.png)`, not a path from the site root. Restructuring into the
one-post-per-slug-folder layout Quarto's own examples favour would break every
one of those references in every post at once. Keeping the tree identical means
a ported post needs no image edits at all.

## Local development

Quarto is a single binary and installs without sudo:

```bash
curl -L https://github.com/quarto-dev/quarto-cli/releases/download/v1.10.18/quarto-1.10.18-macos.tar.gz -o /tmp/quarto.tar.gz
mkdir -p ~/.local/quarto && tar -xzf /tmp/quarto.tar.gz -C ~/.local/quarto --strip-components=1
ln -sf ~/.local/quarto/bin/quarto ~/.local/bin/quarto
```

Pin 1.10.18 — the same version CI uses. The pre-render script needs `pyyaml`
and nothing else, because no notebook cell is ever executed:

```bash
uv venv .venv && uv pip install -r requirements.txt
quarto preview
```

`.venv/bin/python` is the interpreter named in `_quarto.yml`, so the virtualenv
has to be at that path and not somewhere uv chose.

## Deployment

Push to `main` → [`deploy-cloudflare.yml`](.github/workflows/deploy-cloudflare.yml)
installs Quarto, creates the pre-render environment, runs `quarto render` and
deploys `_site` to Cloudflare Workers static assets. `workflow_dispatch` runs
the same job by hand. There is no build configuration in the Cloudflare
dashboard.

Required repository secrets (Settings → Secrets and variables → Actions), both
already set:

| Secret | Notes |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | Custom token with the **Edit Cloudflare Workers** template |
| `CLOUDFLARE_ACCOUNT_ID` | 32 hex characters |

Keep `wrangler.json`'s `assets.directory` (`./_site`) in step with
`_quarto.yml`'s `output-dir`.

## Gotchas worth knowing

Each of these was measured on this repo, not inferred from documentation.

- **`{{< include >}}` is expanded before pre-render runs.** Quarto resolves
  includes while reading the file, which happens earlier in the pipeline than
  the pre-render step, so a file that `tools/prerender.py` generates cannot be
  included — the include resolves against whatever was on disk before the
  script ran, or fails outright on a cold checkout. Generated content is
  therefore injected by editing the target files in place instead.
- **Pandoc ends a raw HTML block at the first blank line.** python-markdown
  does not, so the hand-written JS widgets carried over from the MkDocs posts
  have blank lines inside their markup. Under Pandoc everything after that
  first blank line stops being HTML and renders as escaped code, and because
  the opening half of the widget did render, every `getElementById` in the
  script returns `null`. Wrap those widgets in ```` ```{=html} ```` fences. The
  render log says nothing about it; the browser console is where you find it.
- **`scss:defaults` layers compile in reverse listing order.** Quarto reverses
  the theme list when assembling the defaults layer, so in
  `[cosmo, _palette-light.scss, custom.scss]` the `custom.scss` defaults are
  compiled *before* `_palette-*.scss`. `custom.scss` cannot read a variable
  defined in the generated palette file; it has to define or duplicate what it
  needs.
- **Cloudflare's 25 MiB per-asset cap.** The whole deploy is rejected with
  `Asset too large.` if any single file in `_site` exceeds it — a per-file
  limit, not a total, and the error does not name the offending file.
- **Wrangler must be v4.** The action's default is 3.90, which predates
  assets-only Workers and fails with `Missing entry-point` on a config that has
  `assets` but no `main`. Hence `wranglerVersion: "4"` in the workflow.
- **`fetch-depth: 0` is load-bearing.** The curriculum pre-render step falls
  back to `git log` for any post without an explicit date. With a shallow clone
  that becomes file mtime — checkout time on a fresh runner — so those posts all
  get the same date and the homepage ordering collapses.

## Generated files

`_palette-light.scss`, `_palette-dark.scss` and `_sidebar.yml` are written by
`tools/prerender.py` on every render and are gitignored, same treatment as the
generated stylesheets in the MkDocs repo. Do not edit them; edit
`tools/palette.yml` and `series/*.yml`. `_site/` and `.quarto/` are likewise
build output.
