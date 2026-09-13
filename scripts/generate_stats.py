#!/usr/bin/env python3
"""Draw the profile README's stat graphics from the GitHub GraphQL API.

No runtime dependencies beyond the standard library. Profile statistics come
from GitHub; the visitor snapshot keeps the existing Komarev count and renders
it with Anime Counter's Naruto digit theme.

Outputs (all sharing one visual language with ascii.svg):
  stats.svg   hero total + weekly sparkline
  streak.svg  current and longest streak
  langs.svg   top languages, by bytes and by repo count
  year.svg    the year as a character map, in the portrait's own ramp
  views.svg   preserved profile views rendered as anime character digits

Every file uses the portrait's grey ink, a monospace face, a transparent
background, and the same left-to-right clipPath reveal with a cursor riding
the edge. Motion is SMIL because GitHub strips <script> from READMEs.

Env vars:
  GITHUB_TOKEN  required — ${{ secrets.GITHUB_TOKEN }} in the Action
  GH_LOGIN      user to summarise (default: 0nsku)
  OUT_DIR       where to write the SVGs (default: repo root)
"""
import base64
import collections
import concurrent.futures
import functools
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

API = "https://api.github.com/graphql"

# Two things are pinned for determinism, both learned the hard way:
#  * the contribution window uses whole UTC days — otherwise "the past year"
#    is measured from request time and days drift between week buckets,
#    moving the sparkline a fraction of a pixel and committing noise nightly;
#  * private contributions are included — the profile's "last year" total
#    includes private activity, and the public-only default is misleading when
#    compared with the signed-in GitHub profile.
QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  viewer { login }
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { contributionCount date weekday } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 privacy: PUBLIC) {
      nodes {
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
    privateRepositories: repositories(first: 1, ownerAffiliations: OWNER,
                                      isFork: false, privacy: PRIVATE) {
      totalCount
    }
  }
}
"""

# Portrait ink = data ink, so every graphic reads as one material.
LIGHT = dict(data="#6d28d9", emph="#4c1d95", dim="#7c3aed",
             rule="#ddd6fe", surface="#ffffff")
DARK  = dict(data="#a78bfa", emph="#ddd6fe", dim="#8b5cf6",
             rule="#3b2a57", surface="#0d1117")
# JBMono is the inlined subset; the rest is a fallback.
MONO = ("JBMono,ui-monospace,SFMono-Regular,Menlo,Consolas,"
        "&apos;Liberation Mono&apos;,monospace")
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


@functools.lru_cache(maxsize=None)
def face(filename, weight):
    """One @font-face rule with the subset inlined as a data URI.

    External font URLs cannot work here: these SVGs are loaded through <img>,
    and browsers refuse to fetch subresources for an image document. Inlining
    also pins the advance width — the portrait grid assumes 0.600 em.
    """
    with open(os.path.join(FONT_DIR, filename), "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return (f"@font-face{{font-family:JBMono;font-style:normal;"
            f"font-weight:{weight};font-display:block;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")


def font_text():
    """Basic latin, both weights — for the data graphics."""
    return face("jbmono-400.woff2", 400) + face("jbmono-600.woff2", 600)


def font_head():
    """Only the letters the section headings use."""
    return face("jbmono-head.woff2", 600)


WIDTH  = 620    # every graphic shares one column width
LEFT   = 34     # shared left inset (year.svg needs it for the weekday gutter)
REVEAL = 1.30   # seconds; matches the portrait's cadence
RAMP   = [" ", ":", "+", "#", "@"]   # portrait's own ramp, quiet → loud
MON    = ["jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec"]


# ---------------------------------------------------------------- data

def window():
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    return (f"{start.isoformat()}T00:00:00Z", f"{today.isoformat()}T23:59:59Z")


def fetch(login, token):
    since, until = window()
    body = json.dumps({"query": QUERY,
                       "variables": {"login": login,
                                     "from": since, "to": until}}).encode()
    req = urllib.request.Request(
        API, data=body,
        headers={"Authorization": f"bearer {token}",
                 "Content-Type": "application/json",
                 "User-Agent": f"{login}-profile-stats"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if "errors" in payload:
        raise SystemExit(f"GraphQL errors: {payload['errors']}")
    data = payload.get("data") or {}
    user = data.get("user")
    if not user:
        raise SystemExit(f"no such user: {login}")
    user["_viewerLogin"] = (data.get("viewer") or {}).get("login")
    user["_privateRepoCount"] = (user.get("privateRepositories") or {}).get("totalCount", 0)
    return user


def fetch_views(login):
    """Read the existing Komarev counter, then request an anime snapshot.

    The README still contains Komarev's invisible pixel, so the original
    counter continues accumulating views. This function only changes how the
    saved number is presented.
    """
    query = urllib.parse.urlencode({"username": login, "style": "flat-square"})
    req = urllib.request.Request(
        f"https://komarev.com/ghpvc/?{query}",
        headers={"User-Agent": f"{login}-profile-stats"})
    with urllib.request.urlopen(req, timeout=30) as response:
        badge = response.read().decode("utf-8")
    matches = re.findall(r">([0-9][0-9,]*)</text>", badge)
    if not matches:
        raise RuntimeError("could not read the preserved Komarev view count")
    count = int(matches[-1].replace(",", ""))

    anime_query = urllib.parse.urlencode({
        "theme": "naruto",
        "length": "7",
        "scale": "1",
        "pixelated": "1",
        "num": str(count),
    })
    anime_url = ("https://anime-counter.lulushu.workers.dev/"
                 f"@{login}-snapshot?{anime_query}")
    anime_req = urllib.request.Request(
        anime_url, headers={"User-Agent": f"{login}-profile-stats"})
    with urllib.request.urlopen(anime_req, timeout=30) as response:
        return count, response.read()


def pretty(iso):
    d = date.fromisoformat(iso)
    return f"{MON[d.month - 1]} {d.day}"


def streaks(days):
    """Current and longest runs of days with at least one contribution.

    A zero on the final day doesn't break the current streak — the day
    isn't over yet. Any earlier zero does.
    """
    best = dict(length=0, start=None, end=None)
    run, run_start = 0, None
    for d in days:
        if d["contributionCount"] > 0:
            run += 1
            run_start = run_start or d["date"]
            if run > best["length"]:
                best = dict(length=run, start=run_start, end=d["date"])
        else:
            run, run_start = 0, None

    cur = dict(length=0, start=None, end=None)
    tail = days[:-1] if days and days[-1]["contributionCount"] == 0 else days
    for d in reversed(tail):
        if d["contributionCount"] == 0:
            break
        cur["length"] += 1
        cur["start"]   = d["date"]
        cur["end"]     = cur["end"] or d["date"]
    return cur, best


def languages(repos):
    by_size, by_repo = {}, {}
    for node in repos:
        edges = (node.get("languages") or {}).get("edges") or []
        for e in edges:
            name = e["node"]["name"]
            by_size[name] = by_size.get(name, 0) + e["size"]
        if edges:
            top = edges[0]["node"]["name"]
            by_repo[top] = by_repo.get(top, 0) + 1

    def rank(d):
        # sort by value then name — equal values must never reorder between runs
        return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    return rank(by_size), rank(by_repo)


def summarise(user):
    cal   = user["contributionsCollection"]["contributionCalendar"]
    weeks = [w["contributionDays"] for w in cal["weeks"]]
    days  = [d for w in weeks for d in w]
    weekly = [sum(d["contributionCount"] for d in w) for w in weeks]
    cur, best = streaks(days)
    by_size, by_repo = languages(user["repositories"]["nodes"])
    return dict(
        source="github",
        total=cal["totalContributions"],
        active=sum(1 for d in days if d["contributionCount"] > 0),
        best_week=max(weekly) if weekly else 0,
        weekly=weekly, weeks=weeks,
        current=cur, longest=best,
        by_size=by_size, by_repo=by_repo)


def rest_json(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "0nsku-profile-stats",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def private_activity(login, token):
    """Build a private-aware calendar directly from owned repositories.

    GitHub's contributionCalendar can be empty when private activity sharing is
    disabled. An owner token can still read the underlying default-branch
    commits, so this reconstructs the chart directly from GitHub.
    """
    repos = []
    page = 1
    while True:
        batch = rest_json(
            "https://api.github.com/user/repos?affiliation=owner&sort=updated"
            f"&per_page=100&page={page}", token)
        repos.extend(repo for repo in batch if not repo.get("fork"))
        if len(batch) < 100:
            break
        page += 1

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)

    def scan(repo):
        full_name = repo["full_name"]
        encoded = urllib.parse.quote(full_name, safe="/")
        lang_bytes = collections.Counter()
        try:
            lang_bytes.update(rest_json(
                f"https://api.github.com/repos/{encoded}/languages", token))
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 409):
                raise

        commits = collections.Counter()
        page_number = 1
        while True:
            params = urllib.parse.urlencode({
                "author": login,
                "sha": repo.get("default_branch") or "HEAD",
                "since": f"{start.isoformat()}T00:00:00Z",
                "until": f"{today.isoformat()}T23:59:59Z",
                "per_page": 100,
                "page": page_number,
            })
            try:
                batch = rest_json(
                    f"https://api.github.com/repos/{encoded}/commits?{params}", token)
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 409):
                    break
                raise
            for item in batch:
                stamp = item["commit"]["author"]["date"][:10]
                commits[stamp] += 1
            if len(batch) < 100:
                break
            page_number += 1
        return lang_bytes, repo.get("language"), commits

    by_size = collections.Counter()
    by_repo = collections.Counter()
    counts = collections.Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for lang_bytes, main_language, commits in pool.map(scan, repos):
            by_size.update(lang_bytes)
            counts.update(commits)
            if main_language:
                by_repo[main_language] += 1

    weeks = []
    week = []
    cursor = start
    while cursor <= today:
        weekday = (cursor.weekday() + 1) % 7
        if weekday == 0 and week:
            weeks.append(week)
            week = []
        week.append({
            "contributionCount": counts[cursor.isoformat()],
            "date": cursor.isoformat(),
            "weekday": weekday,
        })
        cursor += timedelta(days=1)
    if week:
        weeks.append(week)

    days = [day for group in weeks for day in group]
    weekly = [sum(day["contributionCount"] for day in group) for group in weeks]
    current, longest = streaks(days)

    def rank(counter):
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:5]

    return dict(
        source="github-private",
        total=sum(counts.values()),
        active=sum(1 for day in days if day["contributionCount"] > 0),
        best_week=max(weekly) if weekly else 0,
        weekly=weekly,
        weeks=weeks,
        current=current,
        longest=longest,
        by_size=rank(by_size),
        by_repo=rank(by_repo),
    )


def cached_activity(path):
    try:
        with open(path, encoding="utf-8") as cache_file:
            return json.load(cache_file)
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- drawing

def style(extra="", font=None):
    def block(t):
        return (f".d-f{{fill:{t['data']}}}.d-s{{stroke:{t['data']}}}"
                f".e-f{{fill:{t['emph']}}}.m-f{{fill:{t['dim']}}}"
                f".u-s{{stroke:{t['rule']}}}.r{{stroke:{t['surface']}}}")
    return (f"<style>{font or font_text()}"
            f"{block(LIGHT)}.w{{fill:{LIGHT['data']};opacity:.13}}{extra}"
            f"@media(prefers-color-scheme:dark){{{block(DARK)}"
            f".w{{fill:{DARK['data']};opacity:.16}}}}</style>")


def head(w, h, font=None):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" fill="none" font-family="{MONO}">'
            + style(font=font))


def fade(delay, dur=0.45):
    return (f'<animate attributeName="opacity" from="0" to="1" '
            f'begin="{delay:.2f}s" dur="{dur}s" fill="freeze"/>')


def wipe(cid, x, y, w, h, delay, dur=REVEAL):
    """clipPath reveal plus the cursor block that rides its edge."""
    clip = (f'<clipPath id="{cid}"><rect x="{x}" y="{y}" height="{h}" width="0">'
            f'<animate attributeName="width" from="0" to="{w}" '
            f'begin="{delay:.2f}s" dur="{dur}s" fill="freeze"/></rect></clipPath>')
    cursor = (f'<rect y="{y}" width="2" height="{h}" class="d-f" opacity="0">'
              f'<animate attributeName="x" from="{x}" to="{x + w}" '
              f'begin="{delay:.2f}s" dur="{dur}s" fill="freeze"/>'
              f'<set attributeName="opacity" to="0.55" begin="{delay:.2f}s"/>'
              f'<set attributeName="opacity" to="0" '
              f'begin="{delay + dur:.2f}s"/></rect>')
    return clip, cursor


def label(x, y, text, size=11, cls="m-f", anchor="start", extra=""):
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}"{a}'
            f'{extra}>{html.escape(str(text))}</text>')


def hbar(x, y, w, h, cls="d-f", r=3.0):
    """Horizontal bar: rounded data-end on the right, square at the baseline."""
    if w <= 0.6:
        return ""
    r = min(r, h / 2.0, w)
    return (f'<path d="M{x:.1f} {y:.1f}H{x + w - r:.1f}'
            f'Q{x + w:.1f} {y:.1f} {x + w:.1f} {y + r:.1f}'
            f'V{y + h - r:.1f}Q{x + w:.1f} {y + h:.1f} {x + w - r:.1f} {y + h:.1f}'
            f'H{x:.1f}Z" class="{cls}"/>')


def draw_stats(s):
    """Hero number, two secondary counts, and the weekly sparkline."""
    H      = 148
    weekly = s["weekly"] or [0]
    peak   = max(weekly) or 1
    p = [head(WIDTH, H)]
    p.append(f'<g opacity="0">{fade(0.10)}'
             + label(0, 50, s["total"], 52, "e-f", extra=' font-weight="600"')
             + label(0, 72, "commits in the last year", 12) + '</g>')
    secondary = [(s["active"], "active days"),
                 (s["best_week"], "best week")]
    for i, (val, lab) in enumerate(secondary):
        p.append(f'<g opacity="0">{fade(0.30 + i * 0.12)}'
                 + label(WIDTH, 30 + i * 40, val, 19, "e-f", "end",
                         ' font-weight="600"')
                 + label(WIDTH, 47 + i * 40, lab, 11, "m-f", "end") + '</g>')

    base, top = H - 10, H - 58
    span = base - top
    step = WIDTH / max(len(weekly) - 1, 1)
    pts  = [(i * step, base - (v / peak) * span) for i, v in enumerate(weekly)]
    clip, cursor = wipe("rs", 0, top - 6, WIDTH, span + 8, 0.50)
    p.append(clip)
    p.append('<g clip-path="url(#rs)">')
    p.append(f'<path d="M{pts[0][0]:.1f} {base:.1f}'
             + "".join(f'L{x:.1f} {y:.1f}' for x, y in pts)
             + f'L{pts[-1][0]:.1f} {base:.1f}Z" class="w"/>')
    p.append(f'<path d="M{pts[0][0]:.1f} {pts[0][1]:.1f}'
             + "".join(f'L{x:.1f} {y:.1f}' for x, y in pts[1:])
             + f'" class="d-s" stroke-width="2" stroke-linejoin="round" '
             f'stroke-linecap="round"/>')
    p.append("</g>")
    p.append(cursor)
    ex, ey = pts[-1]
    p.append(f'<circle cx="{ex - 2:.1f}" cy="{ey:.1f}" r="4.5" class="e-f r" '
             f'stroke-width="2" opacity="0">{fade(0.50 + REVEAL, 0.35)}</circle>')
    p.append("</svg>")
    return "".join(p)


def draw_streak(s):
    """Current and longest streak, split by a hairline."""
    H     = 96
    cells = []
    for k, lab in (("current", "current streak"), ("longest", "longest streak")):
        r    = s[k]
        span = (f"{pretty(r['start'])} &#8211; {pretty(r['end'])}"
                if r["length"] and r.get("start") and r.get("end")
                else "&#8212;")
        cells.append((r["length"], lab, span))

    p   = [head(WIDTH, H)]
    mid = WIDTH / 2
    p.append(f'<line x1="{mid:.0f}" y1="16" x2="{mid:.0f}" y2="80" '
             f'class="u-s" stroke-width="1" opacity="0">{fade(0.20)}</line>')
    for i, (val, lab, span) in enumerate(cells):
        x = LEFT if i == 0 else mid + LEFT
        p.append(f'<g opacity="0">{fade(0.12 + i * 0.14)}'
                 + label(x, 44, f"{val}", 34, "e-f", extra=' font-weight="600"')
                 + label(x, 64, lab, 11)
                 + label(x, 80, span, 10) + '</g>')
    p.append("</svg>")
    return "".join(p)


def draw_langs(s):
    """Two small charts: share of bytes, and count of repos by main language."""
    rows  = max(len(s["by_size"]), len(s["by_repo"]), 1)
    H     = 26 + rows * 22 + 6
    colw  = (WIDTH - LEFT - 30) / 2
    name_w, bar_max = 82, colw - 82 - 44

    p      = [head(WIDTH, H)]
    groups = [(LEFT, "by bytes", s["by_size"], True),
              (LEFT + colw + 30, "by repos", s["by_repo"], False)]
    for gi, (gx, title, data, as_pct) in enumerate(groups):
        p.append(f'<g opacity="0">{fade(0.10 + gi * 0.10)}'
                 + label(gx, 12, title.upper(), 9, "m-f",
                         extra=' letter-spacing="1.3"') + '</g>')
        if not data:
            continue
        top   = max(v for _, v in data) or 1
        total = sum(v for _, v in data) or 1
        cid   = f"rl{gi}"
        clip, cursor = wipe(cid, gx + name_w, 20, bar_max, rows * 22,
                            0.34 + gi * 0.12, 0.95)
        p.append(clip)
        for ri, (name, val) in enumerate(data):
            y     = 26 + ri * 22
            shown = (f"{val / total * 100:.0f}%" if as_pct else f"{val}")
            p.append(f'<g opacity="0">{fade(0.24 + gi * 0.10 + ri * 0.05)}'
                     + label(gx, y + 8, name.lower()[:11], 11, "e-f")
                     + label(gx + colw - 6, y + 8, shown, 11, "m-f", "end")
                     + '</g>')
            p.append(f'<g clip-path="url(#{cid})">'
                     + hbar(gx + name_w, y, bar_max * val / top, 7)
                     + '</g>')
        p.append(cursor)
    p.append("</svg>")
    return "".join(p)


def draw_heading(word):
    """A section heading in the mono face, with a hairline running right.

    GitHub strips <style> and style= from markdown, so a real markdown heading
    can only ever be GitHub's own sans. Rendering the label as an SVG is the
    only way to put the page's own typeface on it.
    """
    FS       = 16
    H        = 26
    text_end = len(word) * FS * 0.6 + 18
    p = [head(WIDTH, H, font=font_head())]
    p.append(label(0, 18, word, FS, "e-f", extra=' font-weight="600"'))
    p.append(f'<line x1="{text_end:.0f}" y1="12.5" x2="{WIDTH}" y2="12.5" '
             f'class="u-s" stroke-width="1"/>')
    p.append("</svg>")
    return "".join(p)


def draw_year(s):
    """Seven rows × fifty-three weeks, intensity encoded as a character."""
    FS, LH, COLW = 9.2, 11.0, 2
    CW           = FS * 0.6
    pad_l, pad_t = LEFT, 44
    weeks        = s["weeks"]
    ncols        = len(weeks) * COLW
    H            = int(pad_t + 7 * LH + 26)

    def level(v):
        for i, cut in enumerate((0, 2, 5, 9)):
            if v <= cut:
                return i
        return 4

    p = [head(WIDTH, H)]
    p.append(f'<g opacity="0">{fade(0.10)}'
             + label(pad_l, 16, "THE YEAR", 9, "m-f",
                     extra=' letter-spacing="1.3"')
             + label(pad_l, 32, f"{s['active']} of "
                     f"{sum(len(w) for w in weeks)} days had a contribution", 11)
             + '</g>')

    lx = WIDTH - 6
    p.append(f'<g opacity="0">{fade(1.30)}'
             + label(lx - 78, 32, "less", 9, "m-f", "end")
             + f'<text xml:space="preserve" x="{lx - 72}" y="32" class="d-f" '
             f'font-size="{FS}">{" ".join(RAMP[1:])}</text>'
             + label(lx, 32, "more", 9, "m-f", "end") + '</g>')

    for r in range(7):
        chars = []
        for w in weeks:
            day = next((d for d in w if d.get("weekday") == r), None)
            v   = day["contributionCount"] if day else 0
            chars.append(RAMP[level(v)] * COLW)
        line = "".join(chars).rstrip()
        if not line:
            continue
        y    = pad_t + r * LH
        w_px = max(len(line), 1) * CW
        cid  = f"ry{r}"
        delay = 0.30 + r * 0.07
        p.append(f'<clipPath id="{cid}"><rect x="{pad_l}" y="{y}" '
                 f'height="{LH}" width="0"><animate attributeName="width" '
                 f'from="0" to="{w_px:.1f}" begin="{delay:.2f}s" dur="0.40s" '
                 f'fill="freeze"/></rect></clipPath>')
        safe = line.replace("&", "&amp;").replace("<", "&lt;")
        p.append(f'<g clip-path="url(#{cid})"><text xml:space="preserve" '
                 f'x="{pad_l}" y="{y + FS - 0.6:.1f}" class="d-f" '
                 f'font-size="{FS}">{safe}</text></g>')

    for r, lab in ((1, "mon"), (3, "wed"), (5, "fri")):
        p.append(label(pad_l - 7, pad_t + r * LH + FS - 0.6, lab, 9, "m-f",
                       "end"))

    last_m, last_x = None, -999.0
    base_y = pad_t + 7 * LH + 13
    for i, w in enumerate(weeks):
        m = int(w[0]["date"][5:7])
        x = pad_l + i * COLW * CW
        if m != last_m and i < len(weeks) - 1 and x - last_x >= 34:
            p.append(label(x, base_y, MON[m - 1], 9, "m-f"))
            last_x = x
        last_m = m

    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------- main

def write(path, svg):
    old = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            old = f.read()
    if old == svg:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return True


def write_bytes(path, content):
    old = b""
    if os.path.exists(path):
        with open(path, "rb") as f:
            old = f.read()
    if old == content:
        return False
    with open(path, "wb") as f:
        f.write(content)
    return True


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")
    login   = os.environ.get("GH_LOGIN", "0nsku")
    out_dir = os.environ.get("OUT_DIR", ".")

    user = fetch(login, token)
    s = summarise(user)
    cache_path = os.path.join(out_dir, "profile-data.json")
    if not s["total"]:
        if (user.get("_viewerLogin") or "").lower() == login.lower() and user.get("_privateRepoCount"):
            print(f"scanning {user['_privateRepoCount']} private repositories...")
            s = private_activity(login, token)
        else:
            fallback = cached_activity(cache_path)
            if fallback:
                s = fallback
    files = {
        "stats.svg":  draw_stats(s),
        "streak.svg": draw_streak(s),
        "langs.svg":  draw_langs(s),
        "year.svg":   draw_year(s),
    }
    for slug, label in (
        ("about", "about"),
        ("languages", "languages"),
        ("frameworks-libraries", "frameworks & libraries"),
        ("tools-platforms", "tools & platforms"),
        ("stats", "stats"),
        ("visitors", "visitors"),
    ):
        files[f"hd-{slug}.svg"] = draw_heading(label)

    changed = [n for n, svg in files.items()
               if write(os.path.join(out_dir, n), svg)]
    if s.get("source") == "github-private":
        cache = json.dumps(s, indent=2, sort_keys=True) + "\n"
        if write(cache_path, cache):
            changed.append("profile-data.json")
    view_count, view_svg = fetch_views(login)
    if write_bytes(os.path.join(out_dir, "views.svg"), view_svg):
        changed.append("views.svg")
    print(f"{s['total']} commits, {s['active']} active days, "
          f"best week {s['best_week']}, current streak "
          f"{s['current']['length']}, longest {s['longest']['length']}")
    print("languages by bytes: "
          + ", ".join(f"{n} {v}" for n, v in s["by_size"]))
    print(f"preserved profile views: {view_count}")
    print("updated: " + (", ".join(sorted(changed)) if changed else "nothing"))


if __name__ == "__main__":
    main()
