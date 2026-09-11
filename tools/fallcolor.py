"""The fall palette, as an object.

`palette.yml` holds the data; this module turns it into something any program
can use. Nothing here needs mkdocs, and only the plotting methods need
matplotlib, so a script, a notebook, a build hook or another repo can all do:

    from fallcolor import Palette
    p = Palette.load()                       # finds palette.yml beside this file
    p["indigo"].hex                          # '#1d4a62'
    p.discrete("default")                    # categorical, <= 4 series
    p.sequential("ember", 9)                 # magnitude
    p.diverging("tide", 255)                 # signed anomaly
    p.contrast("#fff", p["rust"].hex)        # 5.93

A module-level default instance is exposed as `palette`, and the common calls
are re-exported as functions, so `import fallcolor; fallcolor.use()` still works.

For other languages, export the whole thing:

    python -m fallcolor json > palette.json
    python -m fallcolor css  > palette.css
    python -m fallcolor hex  ember

Three kinds of scale, and choosing the wrong kind is worse than choosing the
wrong colours:

    sequential   magnitude in one direction        encodes in lightness
    diverging    signed anomaly about a real zero  encodes in lightness
    discrete     unordered categories              encodes in hue

Lightness survives every kind of colour vision deficiency; hue does not. That is
why the continuous scales are safe at any resolution and the discrete sets cap
at four series.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Iterator, Sequence

import yaml

__all__ = [
    "Palette",
    "Swatch",
    "PaletteError",
    "palette",
    "discrete",
    "sequential",
    "diverging",
    "colormap",
    "centered",
    "use",
    "register",
    "proof",
    "contrast",
]

AA_TEXT = 4.5  # WCAG AA, body text and small labels
AA_LARGE = 3.0  # WCAG AA, large text and non-text edges
MAX_SERIES = 4  # categories a discrete set carries on colour alone


def _srgb_encode(c: float) -> float:
    """Linear light to sRGB. The inverse of Palette._lin."""
    return 12.92 * c if c <= 0.0031308 else 1.055 * max(c, 0.0) ** (1 / 2.4) - 0.055


def _lab_f(t: float) -> float:
    """The CIELAB companding function."""
    return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116


def _lab_f_inv(t: float) -> float:
    return t**3 if t**3 > 0.008856 else (t - 16 / 116) / 7.787


class PaletteError(ValueError):
    """Raised when the palette data is inconsistent or would ship something
    illegible. The mkdocs hook turns this into a failed build."""


@dataclass(frozen=True)
class Swatch:
    """One named colour, plus what may be written on it and how it behaves as
    ink on each ground."""

    name: str
    hex: str
    ink: str = "#ffffff"
    row: str = "tint"  # 'dark' | 'light' | 'tint' | 'ground'
    on_paper: str | None = None  # the variant to use as ink on the light ground
    on_slate: str | None = None  # ... and on the dark ground

    def __str__(self) -> str:
        return self.hex


@dataclass
class Palette:
    """The palette as a whole: hues, roles, scales and the maths to check them."""

    spec: dict
    path: str | None = None
    _swatches: dict[str, Swatch] = field(default_factory=dict, repr=False)

    DEFAULT_FILE = "palette.yml"
    # Linear-RGB -> LMS and back (Viénot, Brettel & Mollon).
    _M = (
        (0.31399022, 0.63951294, 0.04649755),
        (0.15537241, 0.75789446, 0.08670142),
        (0.01775239, 0.10944209, 0.87255922),
    )
    _MI = (
        (5.47221206, -4.64196010, 0.16963708),
        (-1.12524190, 2.29317094, -0.16789520),
        (0.02980165, -0.19318073, 1.16364789),
    )
    # These projections belong to the LMS space _M defines. Mixing in a
    # coefficient derived for another normalisation (the Smith-Pokorny
    # 0.494207/1.24827 pair is the usual trap) yields neon nonsense, not a
    # dichromat's view.
    CVD = {
        "deuteranope": ((1, 0, 0), (0.9513092, 0, 0.04866992), (0, 0, 1)),
        "protanope": ((0, 1.05118294, -0.05116099), (0, 1, 0), (0, 0, 1)),
        "tritanope": ((1, 0, 0), (0, 1, 0), (-0.86744736, 1.86727089, 0)),
    }

    # ── construction ───────────────────────────────────────────────────────
    def __post_init__(self) -> None:
        sw: dict[str, Swatch] = {}
        for name, v in (self.spec.get("hues") or {}).items():
            sw[name] = Swatch(
                name,
                v["hex"],
                v.get("ink", "#ffffff"),
                v.get("row", "dark"),
                v.get("on_paper"),
                v.get("on_slate"),
            )
        for name, v in (self.spec.get("tints") or {}).items():
            sw[name] = Swatch(name, v["hex"], v.get("ink", "#ffffff"), "tint")
        for name, hexv in (self.spec.get("grounds") or {}).items():
            sw.setdefault(name, Swatch(name, hexv, "#000000", "ground"))
        self._swatches = sw

    @classmethod
    def load(cls, path: str | None = None) -> "Palette":
        """Read a palette file. Defaults to `palette.yml` beside this module."""
        path = path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), cls.DEFAULT_FILE
        )
        if not os.path.exists(path):
            raise PaletteError(f"no palette file at {path}")
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {}, path=path)

    # ── data access ────────────────────────────────────────────────────────
    def __getitem__(self, name: str) -> Swatch:
        try:
            return self._swatches[name]
        except KeyError:
            raise PaletteError(
                f"no colour {name!r}; have {', '.join(sorted(self._swatches))}"
            )

    def __contains__(self, name: object) -> bool:
        return name in self._swatches

    def __iter__(self) -> Iterator[Swatch]:
        return iter(self._swatches.values())

    def __len__(self) -> int:
        return len(self._swatches)

    @property
    def hues(self) -> dict[str, Swatch]:
        return {n: self._swatches[n] for n in (self.spec.get("hues") or {})}

    @property
    def tints(self) -> dict[str, Swatch]:
        return {n: self._swatches[n] for n in (self.spec.get("tints") or {})}

    @property
    def grounds(self) -> dict[str, str]:
        return dict(self.spec.get("grounds") or {})

    @property
    def sets(self) -> dict[str, dict]:
        return dict(self.spec.get("sets") or {})

    @property
    def scales(self) -> dict[str, dict]:
        return dict(self.spec.get("scales") or {})

    def role(self, role: str, scheme: str = "light") -> Swatch:
        """The colour playing `role` in `scheme` — 'primary', 'accent', 'ink'…"""
        roles = (self.spec.get("roles") or {}).get(scheme)
        if roles is None:
            raise PaletteError(f"no scheme {scheme!r}")
        if role not in roles:
            raise PaletteError(
                f"no role {role!r} in {scheme}; have {', '.join(sorted(roles))}"
            )
        return self[roles[role]]

    def as_ink(self, hue: str, scheme: str = "light") -> Swatch:
        """`hue` adjusted to stay legible as ink — a line, a dot, a label — on
        that scheme's ground. A fill carries its own contrast; ink does not."""
        s = self[hue]
        key = s.on_paper if scheme == "light" else s.on_slate
        return self[key] if key else s

    @staticmethod
    def alias(hue: str) -> str:
        """`burnt-sienna` -> `sienna`. Hyphen-free, so it is safe as a mermaid
        class name."""
        return hue.rsplit("-", 1)[-1]

    # ── colour maths ───────────────────────────────────────────────────────
    @staticmethod
    def rgb(hexc: str) -> tuple[float, float, float]:
        h = hexc.lstrip("#")
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            raise PaletteError(f"{hexc!r} is not a hex colour")
        return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

    @staticmethod
    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    @classmethod
    def luminance(cls, hexc: str) -> float:
        r, g, b = (cls._lin(c) for c in cls.rgb(hexc))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def contrast(cls, a: str, b: str) -> float:
        """WCAG contrast ratio, 1 to 21. Body text wants 4.5, large text 3."""
        x, y = cls.luminance(a), cls.luminance(b)
        hi, lo = (x, y) if x > y else (y, x)
        return (hi + 0.05) / (lo + 0.05)

    @classmethod
    def lab(cls, c: str | Sequence[float]) -> tuple[float, float, float]:
        r, g, b = (cls._lin(v) for v in (cls.rgb(c) if isinstance(c, str) else c))
        x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
        fx, fy, fz = _lab_f(x), _lab_f(y), _lab_f(z)
        return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

    @classmethod
    def lab_to_hex(cls, L: float, a: float, b: float) -> str:
        fy = (L + 16) / 116
        fx, fz = fy + a / 500, fy - b / 200
        x, y, z = _lab_f_inv(fx) * 0.95047, _lab_f_inv(fy), _lab_f_inv(fz) * 1.08883
        rgb = (
            3.2406 * x - 1.5372 * y - 0.4986 * z,
            -0.9689 * x + 1.8758 * y + 0.0415 * z,
            0.0557 * x - 0.2040 * y + 1.0570 * z,
        )
        return "#%02x%02x%02x" % tuple(
            min(255, max(0, round(_srgb_encode(c) * 255))) for c in rgb
        )

    @classmethod
    def _mul(cls, m, v):
        return [sum(m[i][j] * v[j] for j in range(3)) for i in range(3)]

    @classmethod
    def simulate(cls, hexc: str, kind: str) -> tuple[float, float, float]:
        """The colour as someone with that colour vision deficiency sees it."""
        if kind not in cls.CVD:
            raise PaletteError(f"no simulation {kind!r}; have {', '.join(cls.CVD)}")
        lms = cls._mul(cls._M, [cls._lin(c) for c in cls.rgb(hexc)])
        back = cls._mul(cls._MI, cls._mul(cls.CVD[kind], lms))
        return tuple(
            min(1.0, max(0.0, _srgb_encode(min(1.0, max(0.0, c))))) for c in back
        )

    @classmethod
    def separation(cls, colors: Sequence[str]) -> float:
        """Worst-case ΔE between any two colours, across normal vision and all
        three dichromat simulations. This is the number that says whether colour
        alone can carry a distinction: >=25 fine, 15-25 add a second channel,
        <15 decorative."""
        cols = list(colors)
        if len(cols) < 2:
            return math.inf
        worst = math.inf
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                for kind in (None, *cls.CVD):
                    a = (
                        cls.rgb(cols[i])
                        if kind is None
                        else cls.simulate(cols[i], kind)
                    )
                    b = (
                        cls.rgb(cols[j])
                        if kind is None
                        else cls.simulate(cols[j], kind)
                    )
                    worst = min(worst, math.dist(cls.lab(a), cls.lab(b)))
        return worst

    @classmethod
    def reversal(cls, cols: Sequence[str]) -> float:
        """Largest backward step in lightness, as a percent of the ramp's span,
        worst case across all three dichromat simulations. Zero is monotonic;
        above about 2% shows as a band that is not in the data."""
        worst = 0.0
        for kind in (None, *cls.CVD):
            Ls = [
                cls.lab(cls.rgb(c) if kind is None else cls.simulate(c, kind))[0]
                for c in cols
            ]
            d = [Ls[i + 1] - Ls[i] for i in range(len(Ls) - 1)]
            span = max(Ls) - min(Ls)
            if not span:
                continue
            back = max(0.0, -min(d)) if sum(d) > 0 else max(0.0, max(d))
            worst = max(worst, 100 * back / span)
        return round(worst, 2)

    # ── scales ─────────────────────────────────────────────────────────────
    def _interp(
        self, stops: Sequence[str], n: int, lightness: tuple[float, float]
    ) -> list[str]:
        """Walk the control points in CIELAB, then overwrite L* with a straight
        sweep. Hue and chroma come from the stops; lightness is imposed, and that
        is what keeps the ramp readable to everyone."""
        pts = [self.lab(h) for h in stops]
        out = []
        for i in range(n):
            t = i / (n - 1) if n > 1 else 0.0
            pos = t * (len(pts) - 1)
            lo = min(int(pos), len(pts) - 2)
            f = pos - lo
            _, a, b = (pts[lo][k] + f * (pts[lo + 1][k] - pts[lo][k]) for k in range(3))
            out.append(
                self.lab_to_hex(lightness[0] + t * (lightness[1] - lightness[0]), a, b)
            )
        return out

    def discrete(self, name: str = "default") -> list[str]:
        """A named categorical set, in its declared order. The order is chosen
        for worst-case separation under colour vision deficiency, so truncate
        from the right rather than reordering."""
        if name not in self.sets:
            raise PaletteError(f"no set {name!r}; have {', '.join(self.sets)}")
        return [self[h].hex for h in self.sets[name]["hues"]]

    def sequential(self, name: str = "ember", n: int = 256) -> list[str]:
        cfg = self.scales.get("sequential", {}).get(name)
        if cfg is None:
            raise PaletteError(
                f"no sequential scale {name!r}; "
                f"have {', '.join(self.scales.get('sequential', {}))}"
            )
        return self._interp(
            [self[s].hex for s in cfg["stops"]], n, tuple(cfg["lightness"])
        )

    def diverging(self, name: str = "tide", n: int = 255) -> list[str]:
        """Symmetric about a near-white centre, so equal magnitudes either side
        of zero read equally strong. `n` is forced odd to keep a true midpoint."""
        cfg = self.scales.get("diverging", {}).get(name)
        if cfg is None:
            raise PaletteError(
                f"no diverging scale {name!r}; "
                f"have {', '.join(self.scales.get('diverging', {}))}"
            )
        lo_L, hi_L = self.scales["diverging_lightness"]
        if n % 2 == 0:
            n += 1
        half = n // 2 + 1
        mid = self["paper"].hex
        left = self._interp([self[cfg["low"]].hex, mid], half, (lo_L, hi_L))
        right = self._interp([mid, self[cfg["high"]].hex], half, (hi_L, lo_L))
        return left + right[1:]

    def scale(self, name: str, n: int = 256) -> list[str]:
        """Any scale by name, sequential or diverging."""
        if name in self.scales.get("sequential", {}):
            return self.sequential(name, n)
        if name in self.scales.get("diverging", {}):
            return self.diverging(name, n)
        raise PaletteError(f"no scale {name!r}")

    def scale_names(self) -> dict[str, list[str]]:
        return {k: list(self.scales.get(k, {})) for k in ("sequential", "diverging")}

    def diverging_report(self, name: str) -> dict:
        """A diverging ramp peaks at the centre, so monotonicity is checked per
        arm. `symmetry` is the largest lightness mismatch between the two arms at
        equal distance from zero — if it is big, one direction looks stronger
        than the other for the same magnitude."""
        cols = self.diverging(name)
        mid = len(cols) // 2
        Ls = [self.lab(c)[0] for c in cols]
        return {
            "low_arm": self.reversal(cols[: mid + 1]),
            "high_arm": self.reversal(cols[mid:][::-1]),
            "symmetry": round(
                max(abs(Ls[mid - i] - Ls[mid + i]) for i in range(1, mid + 1)), 1
            ),
            "endpoint_de": round(self.separation([cols[0], cols[-1]]), 1),
        }

    # ── validation ─────────────────────────────────────────────────────────
    def check(self) -> None:
        """Raise on anything that would ship an illegible colour. Called by the
        mkdocs hook, so a bad palette fails the build rather than the reader."""
        # roles must name something real
        for scheme, roles in (self.spec.get("roles") or {}).items():
            for role, name in roles.items():
                if name not in self:
                    raise PaletteError(
                        f"role {scheme}.{role} names {name!r}, which nothing defines"
                    )

        # hue aliases must not collide, or a mermaid class would be ambiguous
        seen: dict[str, str] = {}
        for hue in self.hues:
            a = self.alias(hue)
            if a in seen and seen[a] != hue:
                raise PaletteError(
                    f"hues {seen[a]!r} and {hue!r} share the alias {a!r}"
                )
            seen[a] = hue

        for name, cfg in self.sets.items():
            for hue in cfg.get("hues", []):
                if hue not in self.hues:
                    raise PaletteError(
                        f"set {name!r} names hue {hue!r}, which nothing defines"
                    )

        # a label must be legible on its own fill
        for s in self:
            if s.row == "ground":
                continue
            r = self.contrast(s.ink, s.hex)
            if r < AA_TEXT:
                raise PaletteError(
                    f"ink {s.ink} on {s.name} ({s.hex}) is {r:.2f}:1, below AA {AA_TEXT}"
                )

        # a hue used as ink must clear AA on the ground it is declared for
        g = self.grounds
        for hue, s in self.hues.items():
            for key, gname in (("on_paper", "paper"), ("on_slate", "slate")):
                variant = getattr(s, key)
                if not variant:
                    raise PaletteError(f"hue {hue!r} is missing {key}")
                if variant not in self:
                    raise PaletteError(
                        f"hue {hue!r} {key} names {variant!r}, which nothing defines"
                    )
                r = self.contrast(self[variant].hex, g[gname])
                if r < AA_TEXT:
                    raise PaletteError(
                        f"{hue}.{key} = {variant} is {r:.2f}:1 on {gname}, below AA {AA_TEXT}"
                    )

        # a chip's edge must be visible against both grounds
        stroke = (self.spec.get("diagram") or {}).get("stroke-on-dark-fill")
        if stroke and stroke in self:
            for gname in ("paper", "slate"):
                r = self.contrast(self[stroke].hex, g[gname])
                if r < AA_LARGE:
                    raise PaletteError(
                        f"diagram stroke {stroke} is {r:.2f}:1 on {gname}, "
                        f"below the {AA_LARGE} needed for a visible edge"
                    )

        # a ramp whose lightness doubles back reads as a false band in the data
        for name in self.scales.get("sequential", {}):
            rev = self.reversal(self.sequential(name))
            if rev > 2.0:
                raise PaletteError(
                    f"sequential scale {name!r} reverses lightness by {rev}% of its span"
                )
        for name in self.scales.get("diverging", {}):
            r = self.diverging_report(name)
            if max(r["low_arm"], r["high_arm"]) > 2.0:
                raise PaletteError(
                    f"diverging scale {name!r} is not monotonic within an arm: {r}"
                )
            if r["symmetry"] > 3.0:
                raise PaletteError(
                    f"diverging scale {name!r} arms differ by {r['symmetry']} L*; "
                    "one side would read stronger than the other for the same magnitude"
                )

    def weak_sets(self, floor: float = 15.0) -> dict[str, float]:
        """Sets whose separation is too low to carry data on colour alone."""
        out = {}
        for name in self.sets:
            de = self.separation(self.discrete(name))
            if de < floor:
                out[name] = round(de, 1)
        return out

    # ── generation ─────────────────────────────────────────────────────────
    SCHEME_SELECTOR = {
        "light": '[data-md-color-scheme="default"]',
        "dark": '[data-md-color-scheme="slate"]',
    }
    SLATE_HUE = 20

    @staticmethod
    def rgba(hexc: str, alpha: float) -> str:
        r, g, b = (round(c * 255) for c in Palette.rgb(hexc))
        return f"rgba({r}, {g}, {b}, {alpha})"

    def css(self) -> str:
        """The theme stylesheet: --fall-* tokens, --ink-* tokens, and the
        Material variables mapped onto them."""
        L = [
            "/* GENERATED from palette.yml by fallcolor.Palette. Do not edit. */",
            "",
            ":root {",
        ]
        for group, label in (
            ("hues", "the nine hues"),
            ("tints", "tints"),
            ("grounds", "grounds"),
        ):
            L.append(f"  /* {label} */")
            for name in self.spec.get(group) or {}:
                L.append(f"  --fall-{name}: {self[name].hex};")
        L.append("}")

        for scheme, roles in (self.spec.get("roles") or {}).items():
            L += ["", f"{self.SCHEME_SELECTOR[scheme]} {{"]
            if scheme == "dark":
                L.append(f"  --md-hue: {self.SLATE_HUE};")
            for role, name in roles.items():
                L.append(f"  --fall-{role}: var(--fall-{name});")
            L.append("")
            L.append("  /* --ink-<hue>: legible AS INK on this scheme's ground. */")
            for hue in self.hues:
                L.append(f"  --ink-{hue}: var(--fall-{self.as_ink(hue, scheme).name});")
            r = dict(roles)
            L += [
                "",
                f"  --md-primary-fg-color: var(--fall-{r['primary']});",
                f"  --md-primary-fg-color--light: var(--fall-{r['primary-light']});",
                f"  --md-primary-fg-color--dark: var(--fall-{r['primary-dark']});",
                f"  --md-primary-bg-color: {self[r['primary']].ink};",
                "  --md-primary-bg-color--light: rgba(255, 255, 255, 0.7);",
                f"  --md-accent-fg-color: var(--fall-{r['accent']});",
                f"  --md-accent-fg-color--transparent: {self.rgba(self[r['accent']].hex, 0.1)};",
                "  --md-accent-bg-color: #fff;",
                "  --md-accent-bg-color--light: rgba(255, 255, 255, 0.7);",
                f"  --md-typeset-a-color: var(--fall-{r['link']});",
                f"  --md-footer-bg-color: var(--fall-{r['footer']});",
                f"  --lvl-1: var(--fall-{r['lvl-1']});",
                f"  --lvl-2: var(--fall-{r['lvl-2']});",
                f"  --lvl-3: var(--fall-{r['lvl-3']});",
            ]
            if scheme == "light":
                L += [
                    f"  --md-default-bg-color: var(--fall-{r['ground']});",
                    f"  --md-code-bg-color: var(--fall-{r['ground-tint']});",
                ]
            L.append("}")
        return "\n".join(L) + "\n"

    def diagram_css(self) -> str:
        """Utility classes for inline SVG and HTML, plus the binned scales."""
        L = [
            "/* GENERATED from palette.yml by fallcolor.Palette. Do not edit. */",
            "",
            "/* Hue as ink: follows the scheme, so a line stays visible in both. */",
        ]
        for name in self.hues:
            L += [
                f".fill-{name} {{ fill: var(--ink-{name}); }}",
                f".stroke-{name} {{ stroke: var(--ink-{name}); }}",
            ]
        L += ["", "/* Literal hue, for a fill that must not move. */"]
        for name in list(self.hues) + list(self.tints):
            L += [
                f".fill-{name}-flat {{ fill: var(--fall-{name}); }}",
                f".stroke-{name}-flat {{ stroke: var(--fall-{name}); }}",
            ]
        L += [
            "",
            "/* Role-based, so a figure follows the scheme without naming a hue. */",
        ]
        for role in ("ink", "ink-strong", "primary", "accent"):
            L += [
                f".fill-{role} {{ fill: var(--fall-{role}); }}",
                f".stroke-{role} {{ stroke: var(--fall-{role}); }}",
            ]

        L += [
            "",
            "/* Continuous scales, 9 bins. Sequential runs light to dark;",
            "   diverging runs end -> near-white centre -> other end. */",
            ":root {",
        ]
        for name in self.scales.get("sequential", {}):
            for i, c in enumerate(self.sequential(name, 9)):
                L.append(f"  --seq-{name}-{i}: {c};")
        for name in self.scales.get("diverging", {}):
            for i, c in enumerate(self.diverging(name, 9)):
                L.append(f"  --div-{name}-{i}: {c};")
        L.append("}")
        L += ["", "/* Bin classes, for a heat table or choropleth built in HTML. */"]
        for prefix, kind in (("seq", "sequential"), ("div", "diverging")):
            for name in self.scales.get(kind, {}):
                for i in range(9):
                    L.append(
                        f".{prefix}-{name}-{i} {{ fill: var(--{prefix}-{name}-{i}); "
                        f"background-color: var(--{prefix}-{name}-{i}); }}"
                    )
        L += [
            "",
            "/* Inline SVG inherits the page's text colour unless told otherwise. */",
            ".md-typeset svg .axis { stroke: var(--md-default-fg-color); }",
            ".md-typeset svg .grid { stroke: var(--md-default-fg-color--light); opacity: 0.16; }",
            ".md-typeset svg .label { fill: var(--md-default-fg-color--light); }",
            ".md-typeset svg .label-strong { fill: var(--md-default-fg-color); }",
        ]
        return "\n".join(L) + "\n"

    def classdefs(self, set_name: str = "default", indent: str = "    ") -> list[str]:
        """Mermaid `classDef` lines for a named set: c1, c2, … plus every hue by
        name and by its one-word alias."""
        d = self.spec.get("diagram") or {}
        width = d.get("stroke-width", "1.5px")

        def line(cls: str, hue: str) -> str:
            s = self[hue]
            stroke = (
                d["stroke-on-dark-fill"]
                if s.row == "dark"
                else d["stroke-on-light-fill"]
            )
            return (
                f"{indent}classDef {cls} fill:{s.hex},stroke:{self[stroke].hex},"
                f"stroke-width:{width},color:{s.ink}"
            )

        out = [line(f"c{i}", h) for i, h in enumerate(self.sets[set_name]["hues"], 1)]
        for hue in self.hues:
            out.append(line(hue, hue))
            if self.alias(hue) != hue:
                out.append(line(self.alias(hue), hue))
        return out

    # ── export ─────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Everything resolved to hex, for any program in any language."""
        return {
            "hues": {
                n: {
                    "hex": s.hex,
                    "ink": s.ink,
                    "row": s.row,
                    "on_paper": self.as_ink(n, "light").hex,
                    "on_slate": self.as_ink(n, "dark").hex,
                }
                for n, s in self.hues.items()
            },
            "tints": {n: s.hex for n, s in self.tints.items()},
            "grounds": self.grounds,
            "roles": {
                sc: {r: self[n].hex for r, n in rs.items()}
                for sc, rs in (self.spec.get("roles") or {}).items()
            },
            "sets": {
                n: {
                    "hues": self.discrete(n),
                    "min_delta_e": round(self.separation(self.discrete(n)), 1),
                    "max_series": MAX_SERIES,
                }
                for n in self.sets
            },
            "scales": {
                "sequential": {
                    n: self.sequential(n, 9) for n in self.scales.get("sequential", {})
                },
                "diverging": {
                    n: self.diverging(n, 9) for n in self.scales.get("diverging", {})
                },
            },
        }

    def to_json(self, **kw) -> str:
        kw.setdefault("indent", 2)
        return json.dumps(self.to_dict(), **kw)

    # ── matplotlib ─────────────────────────────────────────────────────────
    def colormap(self, name: str, n: int = 256):
        from matplotlib.colors import LinearSegmentedColormap

        cols = (
            self.sequential(name, n)
            if name in self.scales.get("sequential", {})
            else self.diverging(name, n if n % 2 else n + 1)
        )
        return LinearSegmentedColormap.from_list(f"fall.{name}", cols)

    def register(self, force: bool = False) -> None:
        """Expose every scale as `cmap="fall.<name>"`, plus `_r` reverses.
        Idempotent, so importing twice is not an error."""
        import matplotlib
        from matplotlib.colors import LinearSegmentedColormap

        def add(obj, key):
            if force or key not in matplotlib.colormaps:
                matplotlib.colormaps.register(obj, name=key, force=True)

        for kind in ("sequential", "diverging"):
            for name in self.scales.get(kind, {}):
                cm = self.colormap(name)
                add(cm, f"fall.{name}")
                add(cm.reversed(), f"fall.{name}_r")
        add(
            LinearSegmentedColormap.from_list(
                "fall.paper", [self["paper"].hex, self["burnt-umber"].hex]
            ),
            "fall.paper",
        )

    @staticmethod
    def centered(data, vmax=None):
        """A norm pinning the diverging midpoint to zero. Without it matplotlib
        puts the pale centre at the middle of the data range, and the map
        misstates where the sign changes."""
        import numpy as np
        from matplotlib.colors import TwoSlopeNorm

        v = float(vmax if vmax is not None else np.nanmax(np.abs(np.asarray(data))))
        return TwoSlopeNorm(vmin=-v, vcenter=0.0, vmax=v)

    def use(self, set_name: str = "default") -> None:
        """Apply the palette to matplotlib globally."""
        import matplotlib as mpl
        from cycler import cycler

        self.register()
        g = self.grounds
        mpl.rcParams.update(
            {
                "axes.prop_cycle": cycler(color=self.discrete(set_name)),
                "figure.facecolor": g["paper"],
                "axes.facecolor": g["paper"],
                "savefig.facecolor": g["paper"],
                "axes.edgecolor": self["burnt-sienna"].hex,
                "axes.labelcolor": self["burnt-umber"].hex,
                "text.color": self["burnt-umber"].hex,
                "xtick.color": self["burnt-sienna"].hex,
                "ytick.color": self["burnt-sienna"].hex,
                "grid.color": g["paper-tint"],
                "axes.spines.top": False,
                "axes.spines.right": False,
                "image.cmap": "fall.ember",
                "legend.frameon": False,
            }
        )

    def proof(self, path: str = "fall-scales.png") -> str:
        """Render every scale beside its three dichromat simulations. Run this
        after changing the palette and actually look at it: the numbers say
        whether a scale is safe, the picture says whether it reads well."""
        import numpy as np
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap

        def sim(cols, kind):
            if kind == "normal":
                return cols
            return [
                "#%02x%02x%02x" % tuple(round(c * 255) for c in self.simulate(h, kind))
                for h in cols
            ]

        rows = [
            ("seq", n, self.sequential(n, 256), False)
            for n in self.scales.get("sequential", {})
        ]
        rows += [
            ("div", n, self.diverging(n, 255), False)
            for n in self.scales.get("diverging", {})
        ]
        rows += [("set", n, self.discrete(n), True) for n in self.sets]
        kinds = ["normal", *self.CVD]
        g = self.grounds

        fig, axes = plt.subplots(
            len(rows), len(kinds), figsize=(15.5, 0.62 * len(rows) + 1.4)
        )
        fig.patch.set_facecolor(g["paper"])
        grad = np.linspace(0, 1, 256).reshape(1, -1)
        for i, (tag, name, cols, is_set) in enumerate(rows):
            for j, k in enumerate(kinds):
                ax = axes[i, j]
                data = np.arange(len(cols)).reshape(1, -1) if is_set else grad
                ax.imshow(
                    data,
                    aspect="auto",
                    cmap=ListedColormap(sim(cols, k)),
                    interpolation="nearest" if is_set else "antialiased",
                )
                ax.set_xticks([])
                ax.set_yticks([])
                for sp in ax.spines.values():
                    sp.set_visible(False)
                if j == 0:
                    ax.text(
                        -0.012,
                        0.5,
                        f"{tag} / {name}",
                        transform=ax.transAxes,
                        ha="right",
                        va="center",
                        fontsize=9.5,
                        color=self["burnt-umber"].hex,
                        family="monospace",
                    )
                if i == 0:
                    ax.set_title(
                        k, fontsize=10.5, color=self["burnt-sienna"].hex, pad=9
                    )
        fig.suptitle(
            "Fall palette scales, and how each one survives colour vision deficiency",
            fontsize=13,
            color=self["burnt-umber"].hex,
            y=0.995,
        )
        fig.tight_layout(rect=[0.055, 0.01, 1, 0.965])
        fig.savefig(path, dpi=155, facecolor=g["paper"])
        plt.close(fig)
        return path

    # ── reports ────────────────────────────────────────────────────────────
    def report(self) -> str:
        g = self.grounds
        L = [
            f"{'colour':14}{'hex':9}{'row':7}{'on paper':>9}{'on slate':>9}{'ink on it':>10}"
        ]
        for name, s in {**self.hues, **self.tints}.items():
            L.append(
                f"{name:14}{s.hex:9}{s.row:7}"
                f"{self.contrast(s.hex, g['paper']):9.2f}{self.contrast(s.hex, g['slate']):9.2f}"
                f"{self.contrast(s.ink, s.hex):10.2f}"
            )
        L += ["", f"{'set':12}{'n':>3}{'min ΔE':>8}   verdict"]
        for name in self.sets:
            cols = self.discrete(name)
            de = self.separation(cols)
            v = (
                "colour alone is fine"
                if de >= 25
                else "add a second channel"
                if de >= 15
                else "decorative only"
            )
            L.append(f"{name:12}{len(cols):3}{de:8.1f}   {v}")
        return "\n".join(L)

    def scale_report(self) -> str:
        L = [
            "SEQUENTIAL   reversal = largest backward step in lightness, % of span,",
            "             worst case across all three dichromat simulations. 0 is monotonic.",
        ]
        for name in self.scales.get("sequential", {}):
            cols = self.sequential(name, 9)
            Ls = [self.lab(c)[0] for c in cols]
            L.append(
                f"  {name:9} span {round(max(Ls) - min(Ls)):3} L*   "
                f"reversal {self.reversal(cols):5}%"
            )
            L.append(f"            {' '.join(cols)}")
        L += [
            "",
            "DIVERGING    checked per arm; symmetry is the largest L* mismatch between",
            "             the two arms at equal distance from zero.",
        ]
        for name in self.scales.get("diverging", {}):
            r = self.diverging_report(name)
            L.append(
                f"  {name:9} arms {r['low_arm']}/{r['high_arm']}%  "
                f"symmetry {r['symmetry']} L*  endpoint ΔE {r['endpoint_de']:.0f}"
            )
            L.append(f"            {' '.join(self.diverging(name, 9))}")
        return "\n".join(L)


# ── the default instance, and thin wrappers so the old calls still work ────
palette = Palette.load()

discrete = palette.discrete
sequential = palette.sequential
diverging = palette.diverging
colormap = palette.colormap
centered = palette.centered
use = palette.use
register = palette.register
proof = palette.proof
contrast = palette.contrast
SPEC = palette.spec
HUES = {n: s.hex for n, s in palette.hues.items()}

# Convenience: make the colormaps available to anyone who just imports this
# module. matplotlib is an optional dependency -- the docs build installs only
# the docs group and has none -- so importing must not depend on it. Calling
# register() or use() directly without matplotlib still raises, which is the
# useful behaviour there.
try:
    palette.register()
except ImportError:  # pragma: no cover - depends on the environment
    pass


def _cli(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "report"
    if cmd == "json":
        print(palette.to_json())
    elif cmd == "css":
        print(palette.css(), end="")
    elif cmd == "diagram-css":
        print(palette.diagram_css(), end="")
    elif cmd == "scales":
        print(palette.scale_report())
    elif cmd == "hex":
        name = argv[2] if len(argv) > 2 else "default"
        n = int(argv[3]) if len(argv) > 3 else 9
        cols = (
            palette.discrete(name) if name in palette.sets else palette.scale(name, n)
        )
        print(" ".join(cols))
    elif cmd == "check":
        palette.check()
        weak = palette.weak_sets()
        print("palette OK" + (f"; low separation: {weak}" if weak else ""))
    elif cmd == "proof":
        print(palette.proof(argv[2] if len(argv) > 2 else "fall-scales.png"))
    elif cmd == "report":
        print(palette.report())
    else:
        print(__doc__)
        print(
            "commands: report json css diagram-css scales hex check proof",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
