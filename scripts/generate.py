#!/usr/bin/env python3
"""Render the profile README cards as light and dark SVGs.

Curated content comes from data/profile.json; live numbers (contribution
calendar, languages, stars, followers, pull requests) come from the GitHub
GraphQL API. Every card is written once per theme, so the README can pick
the variant matching the viewer's GitHub theme with <picture>.

Standard library only, so the workflow needs no dependency install.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
WIDTH = 880
PAD = 32
RIGHT = WIDTH - PAD

SANS = "'Segoe UI', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif"
MONO = "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace"

# Pinned so a simple-icons release that drops or renames a slug cannot
# silently change the stack card.
ICON_URL = "https://cdn.jsdelivr.net/npm/simple-icons@15/icons/{slug}.svg"

# Average glyph advance as a fraction of font size. SVG text cannot measure
# itself, so wrapping and inline layout estimate widths from these.
SANS_ADVANCE = 0.53
MONO_ADVANCE = 0.6

THEMES = {
    "dark": {
        "bg": "#0A1226",
        "panel": "#0F1B35",
        "border": "#1E2C4C",
        "text": "#F4F7FF",
        "soft": "#C3D0EA",
        "muted": "#8597BD",
        "accent": "#3B82F6",
        "accent2": "#60A5FA",
        "icon": "#93C5FD",
        "chip": "#122142",
        "chip_border": "#24375F",
        "empty": "#16223F",
        "levels": ["#1E3A8A", "#1D4ED8", "#3B82F6", "#60A5FA"],
        "peak": "#DBEAFE",
        "slices": ["#3B82F6", "#60A5FA", "#93C5FD", "#2563EB", "#1D4ED8", "#BFDBFE", "#1E40AF", "#DBEAFE"],
        "shade_right": 0.74,
        "shade_left": 0.56,
    },
    "light": {
        "bg": "#FFFFFF",
        "panel": "#F4F8FF",
        "border": "#D8E3F6",
        "text": "#0B1B3A",
        "soft": "#33456B",
        "muted": "#5E6F92",
        "accent": "#1D4ED8",
        "accent2": "#2563EB",
        "icon": "#1D4ED8",
        "chip": "#EEF4FF",
        "chip_border": "#CCDBF5",
        "empty": "#E9EFFA",
        "levels": ["#BFDBFE", "#93C5FD", "#3B82F6", "#1D4ED8"],
        "peak": "#0B2A6F",
        "slices": ["#1D4ED8", "#3B82F6", "#60A5FA", "#93C5FD", "#1E3A8A", "#2563EB", "#BFDBFE", "#0B2A6F"],
        "shade_right": 0.86,
        "shade_left": 0.72,
    },
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    followers { totalCount }
    pullRequests { totalCount }
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 20) { edges { size node { name } } }
      }
    }
  }
}
"""


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


def fetch_github(token: str, login: str) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-cards",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL error: {payload['errors']}")
    return payload["data"]["user"]


def fetch_icon(slug: str) -> str | None:
    try:
        with urllib.request.urlopen(ICON_URL.format(slug=slug), timeout=15) as response:
            svg = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"warning: icon '{slug}' unavailable ({error}); using a monogram", file=sys.stderr)
        return None
    match = re.search(r'<path d="([^"]+)"', svg)
    return match.group(1) if match else None


def activity_stats(days: list[tuple[str, int]]) -> dict:
    counts = [count for _, count in days]
    best = run = 0
    for count in counts:
        run = run + 1 if count else 0
        best = max(best, run)

    # Today often has nothing yet; a streak that ran through yesterday is
    # still current, which matches how GitHub and most streak tools count.
    index = len(counts) - 1
    if index >= 0 and counts[index] == 0:
        index -= 1
    current = 0
    while index >= 0 and counts[index]:
        current += 1
        index -= 1

    peak_date, peak = max(days, key=lambda day: day[1])
    return {
        "total": sum(counts),
        "active": sum(1 for count in counts if count),
        "current": current,
        "best": best,
        "peak": peak,
        "peak_date": peak_date,
        "avg": sum(counts) / len(counts),
        "start": days[0][0],
        "end": days[-1][0],
    }


def language_shares(repos: list[dict]) -> list[tuple[str, float]]:
    sizes: dict[str, int] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            sizes[edge["node"]["name"]] = sizes.get(edge["node"]["name"], 0) + edge["size"]
    total = sum(sizes.values()) or 1
    return sorted(((name, size / total) for name, size in sizes.items()), key=lambda item: -item[1])


# --------------------------------------------------------------------------
# SVG helpers
# --------------------------------------------------------------------------


def text(x, y, value, size, fill, *, mono=False, weight=None, anchor=None, spacing=None, extra=""):
    attrs = [
        f'x="{x:.1f}"',
        f'y="{y:.1f}"',
        f'font-size="{size}"',
        f'fill="{fill}"',
        f'class="{"mono" if mono else "sans"}"',
    ]
    if weight:
        attrs.append(f'font-weight="{weight}"')
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    if spacing:
        attrs.append(f'letter-spacing="{spacing}"')
    if extra:
        attrs.append(extra)
    return f'<text {" ".join(attrs)}>{escape(str(value))}</text>'


def width_of(value: str, size: float, mono: bool = False) -> float:
    return len(value) * size * (MONO_ADVANCE if mono else SANS_ADVANCE)


# Bold sans figures are narrower than average text, and separators narrower
# still; sizing them per glyph keeps a trailing caption snug to the number.
FIGURE_ADVANCE = {" ": 0.28, "/": 0.38, ",": 0.28, ".": 0.28}


def figure_width(value: str, size: float) -> float:
    return sum(FIGURE_ADVANCE.get(char, 0.57) for char in value) * size


def wrap(value: str, size: float, width: float, mono: bool = False) -> list[str]:
    limit = max(8, int(width / (size * (MONO_ADVANCE if mono else SANS_ADVANCE))))
    lines: list[str] = []
    line = ""
    for word in value.split():
        candidate = f"{line} {word}".strip()
        if len(candidate) <= limit:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def label(t: dict, x: float, y: float, value: str) -> str:
    return (
        f'<circle cx="{x + 4}" cy="{y - 4}" r="4" fill="{t["accent2"]}"/>'
        + text(x + 16, y, value, 12, t["accent2"], mono=True, weight=600, spacing=1.6)
    )


def shade(color: str, factor: float) -> str:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, round(c * factor))) for c in (r, g, b)))


def pretty_date(iso: str) -> str:
    day = dt.date.fromisoformat(iso)
    return f"{day.day} {day.strftime('%b %Y')}"


def cycle_css(name: str, count: int, step: float) -> str:
    """Keyframes that show one of `count` items at a time, `step` seconds each."""
    on = 100 / count
    fade = min(2.5, on / 6)
    return (
        f"@keyframes {name}{{0%{{opacity:0}}{fade:.2f}%{{opacity:1}}"
        f"{on - fade:.2f}%{{opacity:1}}{on:.2f}%{{opacity:0}}100%{{opacity:0}}}}"
        f".{name}{{opacity:0;animation:{name} {count * step}s infinite}}"
        # Without motion, pin the first item so the card still reads.
        f"@media (prefers-reduced-motion:reduce){{.{name}{{animation:none}}.{name}.first{{opacity:1}}}}"
    )


def document(t: dict, height: int, body: str, title: str, css: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(title)}">'
        f"<title>{escape(title)}</title>"
        f"<style>.sans{{font-family:{SANS}}}.mono{{font-family:{MONO}}}{css}</style>"
        f'<clipPath id="card"><rect x="1" y="1" width="{WIDTH - 2}" height="{height - 2}" rx="16"/></clipPath>'
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="16" fill="{t["bg"]}" stroke="{t["border"]}"/>'
        f'<g clip-path="url(#card)">{body}</g>'
        "</svg>"
    )


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------


def card_hero(p: dict, t: dict) -> str:
    height = 300
    cx, cy, radius = 742, 150, 112
    parts = [
        "<defs>"
        f'<radialGradient id="glow" cx="{cx}" cy="{cy}" r="380" gradientUnits="userSpaceOnUse">'
        f'<stop offset="0" stop-color="{t["accent"]}" stop-opacity="0.30"/>'
        f'<stop offset="1" stop-color="{t["accent"]}" stop-opacity="0"/></radialGradient>'
        f'<pattern id="dots" width="18" height="18" patternUnits="userSpaceOnUse">'
        f'<circle cx="2" cy="2" r="1.1" fill="{t["border"]}"/></pattern>'
        f'<linearGradient id="fade" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
        f'<stop offset="0.45" stop-color="#fff" stop-opacity="1"/></linearGradient>'
        f'<mask id="dotmask"><rect x="420" y="0" width="460" height="{height}" fill="url(#fade)"/></mask>'
        "</defs>",
        f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="url(#dots)" mask="url(#dotmask)"/>',
        f'<rect x="0" y="0" width="{WIDTH}" height="{height}" fill="url(#glow)"/>',
    ]

    # Radar: rings, crosshair, a rotating sweep, and blips that ping in turn.
    for r in (radius / 3, radius * 2 / 3, radius):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" stroke="{t["accent2"]}" stroke-opacity="0.3"/>')
    parts.append(
        f'<path d="M{cx - radius} {cy}H{cx + radius}M{cx} {cy - radius}V{cy + radius}" stroke="{t["accent2"]}" stroke-opacity="0.22"/>'
    )
    sweep_x = cx + radius * math.cos(math.radians(-50))
    sweep_y = cy + radius * math.sin(math.radians(-50))
    parts.append(
        "<g>"
        f'<path d="M{cx} {cy}L{cx + radius} {cy}A{radius} {radius} 0 0 0 {sweep_x:.1f} {sweep_y:.1f}Z" '
        f'fill="{t["accent"]}" fill-opacity="0.16"/>'
        f'<path d="M{cx} {cy}L{cx + radius} {cy}" stroke="{t["accent2"]}" stroke-width="2"/>'
        f'<animateTransform attributeName="transform" type="rotate" from="360 {cx} {cy}" to="0 {cx} {cy}" '
        'dur="6s" repeatCount="indefinite"/></g>'
    )
    for i, (bx, by) in enumerate([(cx + 58, cy - 44), (cx - 70, cy + 24), (cx + 24, cy + 78), (cx - 30, cy - 82)]):
        parts.append(
            f'<circle cx="{bx}" cy="{by}" r="3.5" fill="{t["accent2"]}" opacity="0">'
            f'<animate attributeName="opacity" values="0;1;0.15;0" dur="6s" begin="{i * 1.5}s" repeatCount="indefinite"/>'
            "</circle>"
        )
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="4" fill="{t["accent2"]}"/>')

    parts.append(label(t, PAD, 52, f"{p['login'].upper()} · PROFILE"))
    parts.append(text(PAD, 112, p["name"], 50, t["text"], weight=700, spacing=-0.5))

    # Roles cycle one at a time, each with its own blinking caret.
    step = 3.0
    parts.append(text(PAD, 152, ">", 18, t["muted"], mono=True))
    for i, role in enumerate(p["roles"]):
        caret_x = 56 + width_of(role, 18, mono=True) + 4
        first = " first" if i == 0 else ""
        parts.append(
            f'<g class="role{first}" style="animation-delay:{i * step}s">'
            + text(56, 152, role, 18, t["accent2"], mono=True, weight=600)
            + f'<rect x="{caret_x:.1f}" y="137" width="9" height="19" fill="{t["accent2"]}">'
            '<animate attributeName="opacity" values="1;0;1" dur="1s" repeatCount="indefinite"/></rect>'
            "</g>"
        )
    meta = f"{p['pronouns']}  ·  {p['location']}  ·  {p['education']}"
    parts.append(text(PAD, 188, meta, 15, t["muted"]))

    parts.append(f'<path d="M{PAD} 214H560" stroke="{t["border"]}"/>')
    columns = [PAD, 150, 330]
    for x, item in zip(columns, p["highlights"]):
        parts.append(text(x, 252, item["value"], 22, t["text"], weight=700))
        parts.append(text(x, 274, item["label"], 11, t["muted"], mono=True, spacing=1.2))

    css = cycle_css("role", len(p["roles"]), step)
    return document(t, height, "".join(parts), f"{p['name']} — {', '.join(p['roles'])}", css)


def card_about(p: dict, t: dict) -> str:
    parts = [label(t, PAD, 48, "01 — ABOUT")]
    y = 92
    for line in wrap(p["about"], 16, 492):
        parts.append(text(PAD, y, line, 16, t["soft"]))
        y += 27
    left_end = y

    x0 = 592
    y = 92
    for item in p["now"]:
        parts.append(text(x0, y, item["label"], 11, t["accent2"], mono=True, weight=600, spacing=1.4))
        y += 22
        for line in wrap(item["value"], 15, RIGHT - x0):
            parts.append(text(x0, y, line, 15, t["text"]))
            y += 22
        y += 14
    right_end = y - 14

    height = int(max(left_end, right_end) + 24)
    parts.append(f'<path d="M562 74V{height - 32}" stroke="{t["border"]}"/>')
    return document(t, height, "".join(parts), f"About {p['name']}: {p['about']}")


def card_work(p: dict, t: dict) -> str:
    height = 470
    projects = p["projects"]
    count = len(projects)
    step = 4.0
    pivot_x, pivot_y = 304, 740
    spread = 11 if count > 1 else 0
    parts = [
        label(t, PAD, 48, "02 — SELECTED WORK"),
        text(RIGHT, 48, "EXPLORE ON GITHUB ↗", 11, t["muted"], mono=True, anchor="end", spacing=1.4),
    ]

    # A fan of cards; the active one lifts and gains an outline in step
    # with the detail panel on the right.
    for i, project in enumerate(projects):
        angle = (i - (count - 1) / 2) * spread
        first = " first" if i == 0 else ""
        delay = f"animation-delay:{i * step}s"
        parts.append(
            f'<g transform="translate({pivot_x} {pivot_y}) rotate({angle:.1f})">'
            f'<g class="lift" style="{delay}">'
            f'<rect x="-58" y="-650" width="116" height="400" rx="14" fill="{t["panel"]}" stroke="{t["border"]}"/>'
            f'<rect class="hl{first}" style="{delay}" x="-58" y="-650" width="116" height="400" rx="14" '
            f'fill="none" stroke="{t["accent2"]}" stroke-width="2"/>'
            + text(-42, -622, f"{i + 1:02d}", 13, t["accent2"], mono=True, weight=600)
            + text(-30, -420, project["name"], 20, t["text"], weight=700, extra='transform="rotate(-90 -30 -420)"')
            + "</g></g>"
        )

    parts.append(f'<path d="M566 74V{height - 32}" stroke="{t["border"]}"/>')

    x0 = 592
    panel_width = RIGHT - x0
    for i, project in enumerate(projects):
        first = " first" if i == 0 else ""
        group = [text(x0, 100, f"{i + 1:02d} / {count:02d}", 12, t["muted"], mono=True, spacing=1.4)]
        group.append(text(x0, 140, project["name"], 28, t["text"], weight=700))
        y = 176
        for line in wrap(project["summary"], 14, panel_width):
            group.append(text(x0, y, line, 14, t["soft"]))
            y += 22
        y += 10
        x = x0
        for tag in project["tags"]:
            pill = width_of(tag, 11, mono=True) + 20
            if x + pill > RIGHT:
                x = x0
                y += 32
            group.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{pill:.1f}" height="24" rx="12" '
                f'fill="{t["chip"]}" stroke="{t["chip_border"]}"/>'
                + text(x + pill / 2, y + 16, tag, 11, t["soft"], mono=True, anchor="middle")
            )
            x += pill + 8
        cta = "LIVE DEMO + SOURCE ↗" if project.get("live") else "SOURCE ON GITHUB ↗"
        group.append(text(x0, height - 40, cta, 12, t["accent2"], mono=True, weight=600, spacing=1.4))
        parts.append(f'<g class="panel{first}" style="animation-delay:{i * step}s">{"".join(group)}</g>')

    lifted = 100 / count
    css = (
        cycle_css("panel", count, step)
        + cycle_css("hl", count, step)
        + f"@keyframes lift{{0%{{transform:translateY(0)}}2%{{transform:translateY(-24px)}}"
        f"{lifted - 2:.2f}%{{transform:translateY(-24px)}}{lifted:.2f}%{{transform:translateY(0)}}"
        f"100%{{transform:translateY(0)}}}}"
        f".lift{{animation:lift {count * step}s infinite}}"
        "@media (prefers-reduced-motion:reduce){.lift{animation:none}}"
    )
    names = ", ".join(project["name"] for project in projects)
    return document(t, height, "".join(parts), f"Selected work: {names}", css)


def card_stack(p: dict, t: dict, icons: dict[str, str | None]) -> str:
    parts = [label(t, PAD, 48, "03 — TECH STACK")]
    tile, gap = 56, 20
    x0 = 200
    y = 80
    for group in p["stack"]:
        parts.append(text(PAD, y + 33, group["group"], 11, t["muted"], mono=True, weight=600, spacing=1.4))
        for j, item in enumerate(group["items"]):
            x = x0 + j * (tile + gap)
            parts.append(
                f'<rect x="{x}" y="{y}" width="{tile}" height="{tile}" rx="14" '
                f'fill="{t["chip"]}" stroke="{t["chip_border"]}"/>'
            )
            path = icons.get(item["icon"]) if item["icon"] else None
            if path:
                scale = 26 / 24
                parts.append(
                    f'<path transform="translate({x + 15} {y + 15}) scale({scale:.4f})" d="{path}" fill="{t["icon"]}"/>'
                )
            else:
                parts.append(
                    text(x + tile / 2, y + tile / 2 + 5, item["name"][:3].upper(), 13, t["icon"], mono=True, weight=700, anchor="middle")
                )
            parts.append(text(x + tile / 2, y + tile + 18, item["name"], 11.5, t["muted"], anchor="middle"))
        y += tile + 44

    y += 4
    parts.append(f'<path d="M{PAD} {y}H{RIGHT}" stroke="{t["border"]}"/>')
    y += 30
    parts.append(text(PAD, y + 4, "DOMAINS", 11, t["muted"], mono=True, weight=600, spacing=1.4))
    x = x0
    row_y = y - 14
    for domain in p["domains"]:
        pill = width_of(domain, 12.5) + 26
        if x + pill > RIGHT:
            x = x0
            row_y += 38
        parts.append(
            f'<rect x="{x:.1f}" y="{row_y:.1f}" width="{pill:.1f}" height="28" rx="14" '
            f'fill="{t["chip"]}" stroke="{t["chip_border"]}"/>'
            + text(x + pill / 2, row_y + 18.5, domain, 12.5, t["soft"], anchor="middle")
        )
        x += pill + 8
    height = int(row_y + 28 + PAD)
    names = ", ".join(item["name"] for group in p["stack"] for item in group["items"])
    return document(t, height, "".join(parts), f"Tech stack: {names}")


def card_activity(p: dict, t: dict, days: list[tuple[str, int]], stats: dict) -> str:
    height = 440
    first_day = dt.date.fromisoformat(days[0][0])
    start = first_day - dt.timedelta(days=(first_day.weekday() + 1) % 7)
    cells = []
    for iso, count in days:
        offset = (dt.date.fromisoformat(iso) - start).days
        cells.append((offset // 7, offset % 7, count, iso))

    # Isometric projection: weeks run down-right, weekdays down-left.
    week_axis = (1.0, 0.46)
    day_axis = (-0.82, 0.42)
    tallest = 9.0
    max_count = max(count for *_, count, _ in cells) or 1
    nonzero = sorted(count for *_, count, _ in cells if count)

    def level_color(count: int) -> str:
        if count == stats["peak"]:
            return t["peak"]
        rank = sum(1 for value in nonzero if value < count) / max(1, len(nonzero))
        return t["levels"][min(3, int(rank * 4))]

    def project(x: float, y: float, h: float) -> tuple[float, float]:
        return (x * week_axis[0] + y * day_axis[0], x * week_axis[1] + y * day_axis[1] - h)

    prisms = []
    for wx, wy, count, iso in cells:
        h = 0.18 + tallest * (count / max_count) ** 0.55 if count else 0.18
        prisms.append((wx, wy, h, count))

    gap = 0.14
    points = []
    for wx, wy, h, _ in prisms:
        for px, py in ((wx + gap, wy + gap), (wx + 1 - gap, wy + 1 - gap), (wx + 1 - gap, wy + gap), (wx + gap, wy + 1 - gap)):
            points.append(project(px, py, 0))
            points.append(project(px, py, h))
    min_x = min(x for x, _ in points)
    max_x = max(x for x, _ in points)
    min_y = min(y for _, y in points)
    max_y = max(y for _, y in points)
    box = (PAD + 4, 74, 560, 372)
    scale = min((box[2] - box[0]) / (max_x - min_x), (box[3] - box[1]) / (max_y - min_y))
    off_x = box[0] + ((box[2] - box[0]) - (max_x - min_x) * scale) / 2 - min_x * scale
    off_y = box[1] + ((box[3] - box[1]) - (max_y - min_y) * scale) / 2 - min_y * scale

    def screen(x: float, y: float, h: float) -> str:
        sx, sy = project(x, y, h)
        return f"{sx * scale + off_x:.1f},{sy * scale + off_y:.1f}"

    parts = [label(t, PAD, 48, "04 — ACTIVITY")]
    order = sorted(prisms, key=lambda prism: (prism[0] * week_axis[1] + prism[1] * day_axis[1], prism[0]))
    for wx, wy, h, count in order:
        x1, x2, y1, y2 = wx + gap, wx + 1 - gap, wy + gap, wy + 1 - gap
        top = level_color(count) if count else t["empty"]
        right_face = f"{screen(x2, y1, h)} {screen(x2, y2, h)} {screen(x2, y2, 0)} {screen(x2, y1, 0)}"
        left_face = f"{screen(x1, y2, h)} {screen(x2, y2, h)} {screen(x2, y2, 0)} {screen(x1, y2, 0)}"
        top_face = f"{screen(x1, y1, h)} {screen(x2, y1, h)} {screen(x2, y2, h)} {screen(x1, y2, h)}"
        parts.append(f'<polygon points="{right_face}" fill="{shade(top, t["shade_right"])}"/>')
        parts.append(f'<polygon points="{left_face}" fill="{shade(top, t["shade_left"])}"/>')
        parts.append(f'<polygon points="{top_face}" fill="{top}"/>')

    x0 = 604
    parts.append(f'<path d="M580 74V362" stroke="{t["border"]}"/>')
    parts.append(
        f'<circle cx="{x0 + 4}" cy="{80}" r="4" fill="{t["accent2"]}"/>'
        + text(x0 + 16, 84, "LAST 12 MONTHS", 12, t["accent2"], mono=True, weight=600, spacing=1.6)
    )

    def stat(y: int, caption: str, value: str, suffix: str) -> list[str]:
        out = [text(x0, y, caption, 11, t["muted"], mono=True, weight=600, spacing=1.4)]
        out.append(text(x0, y + 40, value, 36, t["text"], weight=700))
        if suffix:
            out.append(text(x0 + figure_width(value, 36) + 10, y + 40, suffix, 14, t["muted"]))
        return out

    parts += stat(124, "CONTRIBUTIONS", f"{stats['total']:,}", f"{stats['active']} active days")
    parts += stat(204, "STREAK  CURRENT / BEST", f"{stats['current']} / {stats['best']}", "days")
    parts += stat(284, "PEAK DAY", str(stats["peak"]), f"on {pretty_date(stats['peak_date'])}")
    parts.append(text(x0, 364, "AVG / DAY", 11, t["muted"], mono=True, weight=600, spacing=1.4))
    parts.append(text(x0 + 104, 364, f"{stats['avg']:.2f}", 14, t["text"], mono=True, weight=700))

    parts.append(text(PAD, 396, f"@{p['login'].upper()}", 15, t["text"], weight=700, spacing=0.6))
    parts.append(text(PAD, 420, "less", 11, t["muted"], mono=True))
    for i, color in enumerate(t["levels"] + [t["peak"]]):
        parts.append(f'<rect x="{PAD + 38 + i * 15}" y="410" width="11" height="11" rx="2" fill="{color}"/>')
    parts.append(text(PAD + 38 + 5 * 15 + 4, 420, "more", 11, t["muted"], mono=True))
    span = f"{pretty_date(stats['start'])} → {pretty_date(stats['end'])}"
    parts.append(text(RIGHT, 420, span, 11, t["muted"], mono=True, anchor="end"))

    title = (
        f"{stats['total']} contributions in the last 12 months across {stats['active']} active days; "
        f"current streak {stats['current']} days, best {stats['best']}"
    )
    return document(t, height, "".join(parts), title)


def card_languages(p: dict, t: dict, shares: list[tuple[str, float]], numbers: list[tuple[str, int]]) -> str:
    height = 420
    shown = shares[:7]
    rest = sum(share for _, share in shares[7:])
    if rest > 0:
        shown.append(("Other", rest))

    parts = [label(t, PAD, 48, "05 — LANGUAGES")]
    cx, cy, outer, inner = 196, 224, 128, 90
    angle = -math.pi / 2
    gap = 0.014 if len(shown) > 1 else 0
    for i, (_, share) in enumerate(shown):
        sweep = share * 2 * math.pi
        a0, a1 = angle + gap / 2, angle + max(sweep - gap / 2, gap)
        large = 1 if a1 - a0 > math.pi else 0
        ox0, oy0 = cx + outer * math.cos(a0), cy + outer * math.sin(a0)
        ox1, oy1 = cx + outer * math.cos(a1), cy + outer * math.sin(a1)
        ix1, iy1 = cx + inner * math.cos(a1), cy + inner * math.sin(a1)
        ix0, iy0 = cx + inner * math.cos(a0), cy + inner * math.sin(a0)
        parts.append(
            f'<path d="M{ox0:.1f} {oy0:.1f}A{outer} {outer} 0 {large} 1 {ox1:.1f} {oy1:.1f}'
            f'L{ix1:.1f} {iy1:.1f}A{inner} {inner} 0 {large} 0 {ix0:.1f} {iy0:.1f}Z" '
            f'fill="{t["slices"][i % len(t["slices"])]}"/>'
        )
        angle += sweep
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{inner - 10}" fill="none" stroke="{t["border"]}" stroke-dasharray="2 5"/>')
    parts.append(text(cx, cy + 10, str(len(shares)), 46, t["text"], weight=700, anchor="middle"))
    parts.append(text(cx, cy + 36, "languages", 14, t["muted"], anchor="middle"))

    x0 = 392
    bar_x, bar_w = 560, 232
    top_share = shown[0][1] if shown else 1
    for i, (name, share) in enumerate(shown):
        y = 92 + i * 27
        color = t["slices"][i % len(t["slices"])]
        parts.append(f'<rect x="{x0}" y="{y - 10}" width="11" height="11" rx="2" fill="{color}"/>')
        parts.append(text(x0 + 22, y, name, 14, t["text"], weight=600))
        parts.append(f'<rect x="{bar_x}" y="{y - 7}" width="{bar_w}" height="6" rx="3" fill="{t["empty"]}"/>')
        parts.append(
            f'<rect x="{bar_x}" y="{y - 7}" width="{max(3, bar_w * share / top_share):.1f}" height="6" rx="3" fill="{color}"/>'
        )
        pct = f"{share * 100:.1f}%" if share < 0.1 else f"{share * 100:.0f}%"
        parts.append(text(RIGHT, y, pct, 12, t["muted"], mono=True, anchor="end"))

    parts.append(text(x0, 320, f"BY THE NUMBERS · @{p['login'].upper()}", 11, t["muted"], mono=True, weight=600, spacing=1.4))
    tile_gap = 12
    tile_w = (RIGHT - x0 - tile_gap * 3) / 4
    for i, (caption, value) in enumerate(numbers):
        x = x0 + i * (tile_w + tile_gap)
        parts.append(
            f'<rect x="{x:.1f}" y="334" width="{tile_w:.1f}" height="58" rx="10" fill="{t["panel"]}" stroke="{t["border"]}"/>'
        )
        parts.append(text(x + 14, 362, f"{value:,}", 22, t["text"], weight=700))
        parts.append(text(x + 14, 381, caption, 9.5, t["muted"], mono=True, spacing=1.1))

    top = ", ".join(f"{name} {share * 100:.0f}%" for name, share in shown[:4])
    return document(t, height, "".join(parts), f"{len(shares)} languages across public repositories: {top}")


def card_button(t: dict, kind: str) -> str:
    width, height = 196, 48
    caption = {"linkedin": "LINKEDIN", "email": "EMAIL"}[kind]
    if kind == "linkedin":
        glyph = (
            f'<rect x="20" y="15" width="18" height="18" rx="4" fill="{t["accent2"]}"/>'
            + text(29, 28.5, "in", 12, t["bg"], weight=700, anchor="middle")
        )
    else:
        glyph = (
            f'<rect x="20" y="17" width="19" height="14" rx="2.5" fill="none" stroke="{t["accent2"]}" stroke-width="1.8"/>'
            f'<path d="M21 18.5l8.5 6.5 8.5-6.5" fill="none" stroke="{t["accent2"]}" stroke-width="1.8"/>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{caption.title()}"><title>{caption.title()}</title>'
        f"<style>.sans{{font-family:{SANS}}}.mono{{font-family:{MONO}}}</style>"
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="{t["bg"]}" stroke="{t["border"]}"/>'
        + glyph
        + text(52, 29, caption, 13, t["text"], mono=True, weight=600, spacing=1.6)
        + text(width - 22, 29, "↗", 15, t["accent2"], anchor="end")
        + "</svg>"
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="dist", help="directory to write the SVG cards into")
    parser.add_argument("--data", default=str(ROOT / "data" / "profile.json"))
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN (or GH_TOKEN) is required to read contribution data")

    profile = json.loads(Path(args.data).read_text(encoding="utf-8"))
    user = fetch_github(token, profile["login"])

    days = [
        (day["date"], day["contributionCount"])
        for week in user["contributionsCollection"]["contributionCalendar"]["weeks"]
        for day in week["contributionDays"]
    ]
    stats = activity_stats(days)
    repos = user["repositories"]["nodes"]
    shares = language_shares(repos)
    numbers = [
        ("PUBLIC REPOS", user["repositories"]["totalCount"]),
        ("STARS", sum(repo["stargazerCount"] for repo in repos)),
        ("FOLLOWERS", user["followers"]["totalCount"]),
        ("PULL REQUESTS", user["pullRequests"]["totalCount"]),
    ]
    slugs = {item["icon"] for group in profile["stack"] for item in group["items"] if item["icon"]}
    icons = {slug: fetch_icon(slug) for slug in sorted(slugs)}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for theme_name, theme in THEMES.items():
        cards = {
            "hero": card_hero(profile, theme),
            "about": card_about(profile, theme),
            "work": card_work(profile, theme),
            "stack": card_stack(profile, theme, icons),
            "activity": card_activity(profile, theme, days, stats),
            "languages": card_languages(profile, theme, shares, numbers),
            "btn-linkedin": card_button(theme, "linkedin"),
            "btn-email": card_button(theme, "email"),
        }
        for name, svg in cards.items():
            (out / f"{name}-{theme_name}.svg").write_text(svg, encoding="utf-8")

    print(
        f"rendered {len(THEMES) * 8} cards: {stats['total']} contributions, "
        f"streak {stats['current']}/{stats['best']}, {len(shares)} languages"
    )


if __name__ == "__main__":
    main()
