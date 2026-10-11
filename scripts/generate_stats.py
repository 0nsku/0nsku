#!/usr/bin/env python3
"""Draw the profile README's stat graphics from the GitHub GraphQL API.

No runtime dependencies beyond the standard library. Profile statistics come
from GitHub; the visitor snapshot keeps the existing Komarev count and renders
it with Anime Counter's Naruto digit theme.

Outputs (all sharing one visual language with ascii.svg):
  stats.svg   hero total + weekly sparkline
  streak.svg  current and longest streak
  langs.svg   languages weighted by the year's commits, plus active repo count
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
import math
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
query($login: String!, $from: DateTime!, $to: DateTime!, $cyFrom: DateTime!) {
  viewer { login }
  user(login: $login) {
    followers { totalCount }
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { contributionCount date weekday } }
      }
      totalCommitContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      totalRepositoryContributions
      restrictedContributionsCount
      commitContributionsByRepository(maxRepositories: 100) {
        repository {
          nameWithOwner
          languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
            edges { size node { name } }
          }
        }
        contributions { totalCount }
      }
      pullRequestContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
        contributions { totalCount }
      }
      issueContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
        contributions { totalCount }
      }
    }
    thisYear: contributionsCollection(from: $cyFrom, to: $to) {
      totalCommitContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
        contributions { totalCount }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 privacy: PUBLIC) {
      totalCount
      nodes {
        stargazerCount
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
    cy_start = date(today.year, 1, 1)
    return (f"{start.isoformat()}T00:00:00Z", f"{today.isoformat()}T23:59:59Z",
            f"{cy_start.isoformat()}T00:00:00Z")


def fetch(login, token):
    since, until, cy_from = window()
    body = json.dumps({"query": QUERY,
                       "variables": {"login": login,
                                     "from": since, "to": until,
                                     "cyFrom": cy_from}}).encode()
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


def draw_views(count):
    """Compose the anime digit strip from the sprites in assets/views/.

    Digit d shows the committed sprite for that digit, styled after the
    moe-counter rule34 pack — the animated digits aqeu uses.
    """
    CW, CH = 45, 100
    sprite_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "assets", "views")
    digits = str(count).rjust(7, "0")[-7:]
    cells = []
    for i, d in enumerate(digits):
        with open(os.path.join(sprite_dir, f"{d}.gif"), "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        cells.append(f'<image x="{i * CW}" y="0" width="{CW}" height="{CH}" '
                     'image-rendering="pixelated" preserveAspectRatio="none" '
                     f'href="data:image/gif;base64,{b64}"/>')
    w = CW * len(digits)
    return (f'<svg width="{w}" height="{CH}" viewBox="0 0 {w} {CH}" '
            'xmlns="http://www.w3.org/2000/svg">'
            + "".join(cells) + "</svg>")


def fetch_views(login):
    """Read the existing Komarev counter, then render it with our sprites.

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

    return count, draw_views(count).encode()


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


TOP_LANGS = 7

# Scratch/grind repositories that should never show up in the public stats.
# Lowercase full names, comma-separated via the EXCLUDE_REPOS env var.
EXCLUDED_REPOS = {
    name.strip().lower()
    for name in os.environ.get("EXCLUDE_REPOS", "0nsku/meta").split(",")
    if name.strip()
}


def rank_langs(by_size, by_repo):
    # sort by value then name — equal values must never reorder between runs
    rank = lambda d: sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_LANGS]
    return rank(by_size), rank(by_repo)


def languages(repos):
    """All-time fallback: code bytes and primary-language repo counts."""
    by_size, by_repo = {}, {}
    for node in repos:
        edges = (node.get("languages") or {}).get("edges") or []
        for e in edges:
            name = e["node"]["name"]
            by_size[name] = by_size.get(name, 0) + e["size"]
        if edges:
            top = edges[0]["node"]["name"]
            by_repo[top] = by_repo.get(top, 0) + 1
    return rank_langs(by_size, by_repo)


def recent_languages(cc):
    """Languages weighted by recency: per-repo bytes x commits in the
    contribution window, so the chart reflects what the user is actively
    working in rather than their whole all-time codebase."""
    by_size, by_repo = {}, {}
    for item in cc.get("commitContributionsByRepository") or []:
        repo    = item.get("repository") or {}
        if (repo.get("nameWithOwner") or "").lower() in EXCLUDED_REPOS:
            continue
        edges   = (repo.get("languages") or {}).get("edges") or []
        commits = (item.get("contributions") or {}).get("totalCount") or 0
        if not commits:
            continue
        for e in edges:
            name = e["node"]["name"]
            by_size[name] = by_size.get(name, 0) + e["size"] * commits
        if edges:
            top = edges[0]["node"]["name"]
            by_repo[top] = by_repo.get(top, 0) + 1
    return rank_langs(by_size, by_repo)


def excluded_total(collection, key):
    """Contributions GitHub attributes to the excluded repos, per category."""
    return sum(
        (item.get("contributions") or {}).get("totalCount") or 0
        for item in collection.get(key) or []
        if ((item.get("repository") or {}).get("nameWithOwner") or "")
           .lower() in EXCLUDED_REPOS)


def summarise(user):
    cc    = user["contributionsCollection"]
    cal   = cc["contributionCalendar"]
    weeks = [w["contributionDays"] for w in cal["weeks"]]
    days  = [d for w in weeks for d in w]
    weekly = [sum(d["contributionCount"] for d in w) for w in weeks]
    cur, best = streaks(days)
    by_size, by_repo = recent_languages(cc)
    if not by_size:
        by_size, by_repo = languages(user["repositories"]["nodes"])
    busiest = max(days, key=lambda d: d["contributionCount"], default=None)
    cy    = user.get("thisYear") or {}
    repos = user["repositories"]
    excluded = dict(
        commits=excluded_total(cc, "commitContributionsByRepository"),
        commits_cy=excluded_total(cy, "commitContributionsByRepository"),
        prs=excluded_total(cc, "pullRequestContributionsByRepository"),
        issues=excluded_total(cc, "issueContributionsByRepository"))
    return dict(
        source="github",
        total=cal["totalContributions"],
        active=sum(1 for d in days if d["contributionCount"] > 0),
        best_week=max(weekly) if weekly else 0,
        weekly=weekly, weeks=weeks,
        current=cur, longest=best,
        by_size=by_size, by_repo=by_repo,
        busiest_day=(busiest["contributionCount"], busiest["date"])
                    if busiest else (0, None),
        commits_ly=cc.get("totalCommitContributions"),
        commits_cy=cy.get("totalCommitContributions"),
        prs=cc.get("totalPullRequestContributions"),
        reviews=cc.get("totalPullRequestReviewContributions"),
        issues=cc.get("totalIssueContributions"),
        repos_made=cc.get("totalRepositoryContributions"),
        restricted=cc.get("restrictedContributionsCount") or 0,
        stars=sum(n.get("stargazerCount") or 0 for n in repos["nodes"]),
        followers=(user.get("followers") or {}).get("totalCount"),
        public_repos=repos.get("totalCount"),
        private_repos=user.get("_privateRepoCount"),
        excluded=excluded)


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
        repos.extend(repo for repo in batch if not repo.get("fork")
                     and repo["full_name"].lower() not in EXCLUDED_REPOS)
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
            counts.update(commits)
            repo_commits = sum(commits.values())
            if repo_commits:
                for name, size in lang_bytes.items():
                    by_size[name] += size * repo_commits
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
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:TOP_LANGS]

    busiest = max(counts.items(), key=lambda kv: kv[1], default=(None, 0))
    cy_prefix = f"{today.year}-"
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
        busiest_day=(busiest[1], busiest[0]),
        commits_ly=sum(counts.values()),
        commits_cy=sum(v for d, v in counts.items()
                       if d.startswith(cy_prefix)),
        prs=None,
        reviews=None,
        issues=None,
        repos_made=None,
        restricted=None,
        stars=None,
        followers=None,
        public_repos=None,
        private_repos=None,
    )


def excluded_calendar(login, token, start, today):
    """Per-day contribution events on the excluded repos, via REST.

    The contribution calendar can't be filtered by repository, so excluded
    repos' events are subtracted day by day: default-branch commits by the
    user, plus the PRs and issues they opened and the repo creation itself.
    """
    days   = collections.Counter()
    totals = collections.Counter()
    cy_start = date(today.year, 1, 1).isoformat()
    for full in EXCLUDED_REPOS:
        encoded = urllib.parse.quote(full, safe="/")
        try:
            repo = rest_json(f"https://api.github.com/repos/{encoded}", token)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        created = (repo.get("created_at") or "")[:10]
        if created and start <= created <= today.isoformat():
            days[created] += 1
            totals["repos"] += 1

        page = 1
        while True:
            params = urllib.parse.urlencode({
                "author": login,
                "sha": repo.get("default_branch") or "main",
                "since": f"{start}T00:00:00Z",
                "until": f"{today.isoformat()}T23:59:59Z",
                "per_page": 100,
                "page": page,
            })
            try:
                batch = rest_json(
                    f"https://api.github.com/repos/{encoded}/commits?{params}",
                    token)
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 409):
                    break
                raise
            for item in batch:
                stamp = item["commit"]["author"]["date"][:10]
                days[stamp] += 1
                totals["commits"] += 1
                if stamp >= cy_start:
                    totals["commits_cy"] += 1
            if len(batch) < 100:
                break
            page += 1

        page = 1
        while True:
            batch = rest_json(
                f"https://api.github.com/repos/{encoded}/pulls"
                f"?state=all&per_page=100&page={page}", token)
            for item in batch:
                stamp = (item.get("created_at") or "")[:10]
                if start <= stamp <= today.isoformat():
                    days[stamp] += 1
                    totals["prs"] += 1
            if len(batch) < 100:
                break
            page += 1

        # The repo issues endpoint also returns PRs, so search is the
        # cheap way to list just the issues.
        page = 1
        while True:
            try:
                result = rest_json(
                    "https://api.github.com/search/issues"
                    f"?q=repo:{full}+is:issue&per_page=100&page={page}", token)
            except urllib.error.HTTPError:
                break
            items = result.get("items") or []
            for item in items:
                stamp = (item.get("created_at") or "")[:10]
                if start <= stamp <= today.isoformat():
                    days[stamp] += 1
                    totals["issues"] += 1
            if len(items) < 100:
                break
            page += 1
    return days, totals


def apply_exclusions(s, login, token):
    """Subtract excluded-repo activity from every number on the card.

    Category totals come from GraphQL's per-repository breakdown (GitHub's
    own attribution). For the calendar: GitHub does not attribute the
    excluded events to the same dates the REST API reports — it piles most
    of them onto the merge day — so the full excluded total is removed
    greedily from the calendar days the repo's events could have landed on,
    largest days first."""
    if not EXCLUDED_REPOS or s.get("source") != "github":
        return s
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=364)).isoformat()
    edays, etot = excluded_calendar(login, token, start, today)

    ex = s.get("excluded") or {}
    pool = sum(ex.get(k) or etot.get(k) or 0
               for k in ("commits", "prs", "issues")) + etot.get("repos", 0)
    flat = [d for w in s["weeks"] for d in w]
    active = sorted((d for d in flat if edays.get(d["date"])),
                    key=lambda d: -d["contributionCount"])
    removed = 0
    for day in active:
        if pool <= 0:
            break
        cut = min(day["contributionCount"], pool)
        day["contributionCount"] -= cut
        pool -= cut
        removed += cut
    if not removed and not any(etot.values()):
        return s

    days   = [d for w in s["weeks"] for d in w]
    weekly = [sum(d["contributionCount"] for d in w) for w in s["weeks"]]
    busiest = max(days, key=lambda d: d["contributionCount"], default=None)
    ex = s.get("excluded") or {}
    s["total"]      = s["total"] - removed
    s["weekly"]     = weekly
    s["active"]     = sum(1 for d in days if d["contributionCount"] > 0)
    s["best_week"]  = max(weekly) if weekly else 0
    s["busiest_day"] = ((busiest["contributionCount"], busiest["date"])
                        if busiest else (0, None))
    s["current"], s["longest"] = streaks(days)
    for key, gql_key, rest_key in (
            ("commits_ly", "commits", "commits"),
            ("commits_cy", "commits_cy", "commits_cy"),
            ("prs", "prs", "prs"),
            ("issues", "issues", "issues")):
        if s.get(key) is not None:
            s[key] = s[key] - (ex.get(gql_key) or etot.get(rest_key) or 0)
    if s.get("repos_made") is not None:
        s["repos_made"] = s["repos_made"] - etot.get("repos", 0)
    return s


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


def fmt(v):
    return f"{v:,}" if v is not None else None


def draw_stats(s):
    """Hero number, a grid of eight metrics, and the weekly sparkline."""
    H      = 216
    weekly = s["weekly"] or [0]
    peak   = max(weekly) or 1
    p = [head(WIDTH, H)]

    hero_lab = ("commits in the last year"
                if s["source"] == "github-private"
                else "contributions in the last year")
    p.append(f'<g opacity="0">{fade(0.10)}'
             + label(0, 50, fmt(s["total"]), 52, "e-f",
                     extra=' font-weight="600"')
             + label(0, 72, hero_lab, 12))
    sub = []
    if s.get("restricted"):
        sub.append(f"incl. {fmt(s['restricted'])} private")
    if s.get("public_repos") is not None:
        repos = f"{s['public_repos']} public repos"
        if s.get("private_repos"):
            repos += f" + {s['private_repos']} private"
        sub.append(repos)
    if sub:
        p.append(label(0, 90, " · ".join(sub), 10))
    p.append('</g>')

    metrics = [
        (s.get("commits_cy"), "commits this year"),
        (s.get("prs"),        "pull requests"),
        (s.get("issues"),     "issues opened"),
        (s.get("reviews"),    "pr reviews"),
        (s.get("stars"),      "stars earned"),
        (s.get("followers"),  "followers"),
        (s.get("active"),     "active days"),
        (s.get("best_week"),  "best week"),
    ]
    metrics = [(v, l) for v, l in metrics if v is not None]
    ncols = 4
    colw  = (WIDTH - LEFT) / ncols
    for i, (val, lab) in enumerate(metrics):
        col, row = i % ncols, i // ncols
        x = LEFT + col * colw
        y = 116 + row * 34
        p.append(f'<g opacity="0">{fade(0.30 + i * 0.06)}'
                 + label(x, y, fmt(val), 19, "e-f",
                         extra=' font-weight="600"')
                 + label(x, y + 13, lab, 9, "m-f") + '</g>')

    base, top = H - 8, H - 46
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
        span = (f"{pretty(r['start'])} – {pretty(r['end'])}"
                if r["length"] and r.get("start") and r.get("end")
                else "—")
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


LANG_COLORS = {
    "html": "#e34c26", "typescript": "#3178c6", "python": "#3572a5",
    "css": "#663399", "javascript": "#f1e05a", "plpgsql": "#336790",
    "shell": "#89e051", "jupyter notebook": "#da5b0b", "go": "#00add8",
    "rust": "#dea584", "c++": "#f34b7d", "c": "#555555", "c#": "#178600",
    "lua": "#000080", "scss": "#c6538c", "less": "#1d365d", "vue": "#41b883",
    "svelte": "#ff3e00", "php": "#4f5d95", "ruby": "#701516",
    "java": "#b07219", "kotlin": "#a97bff", "swift": "#f05138",
    "dart": "#00b4ab", "solidity": "#aa6746", "makefile": "#427819",
    "dockerfile": "#384d54", "powershell": "#012456", "nix": "#7e7eff",
    "zig": "#ec915c", "elixir": "#6e4a7e", "haskell": "#5e5086",
    "scala": "#c22d40", "r": "#198ce7", "matlab": "#e16737",
    "objective-c": "#438eff", "perl": "#0298c3", "assembly": "#6e4c13",
    "glsl": "#5686a5", "yaml": "#cb171e", "toml": "#9c4221",
    "markdown": "#083fa1", "batchfile": "#c1f12e", "cmake": "#da3434",
}
LANG_RAMP = ["#7c3aed", "#a78bfa", "#6d28d9", "#c4b5fd",
             "#4c1d95", "#8b5cf6", "#ddd6fe"]


def draw_langs(s):
    """Two pie charts: share of activity, and repos by main language."""
    groups = [("by activity", s["by_size"]), ("by repos", s["by_repo"])]
    rows   = max(len(s["by_size"]), len(s["by_repo"]), 1)
    colw   = (WIDTH - LEFT - 30) / 2
    R      = 52
    H      = 30 + max(2 * R + 18, rows * 20) + 8

    p = [head(WIDTH, H)]
    for gi, (title, data) in enumerate(groups):
        gx = LEFT if gi == 0 else LEFT + colw + 30
        p.append(f'<g opacity="0">{fade(0.10 + gi * 0.10)}'
                 + label(gx, 12, title.upper(), 9, "m-f",
                         extra=' letter-spacing="1.3"') + '</g>')
        if not data:
            continue
        total = sum(v for _, v in data) or 1
        cx, cy = gx + R + 4, 30 + R + 10
        lx     = gx + 2 * R + 26
        angle  = -math.pi / 2
        for ri, (name, val) in enumerate(data):
            frac  = val / total
            a2    = angle + frac * 2 * math.pi
            color = LANG_COLORS.get(name.lower(), LANG_RAMP[ri % len(LANG_RAMP)])
            if frac >= 0.9999:
                piece = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{R}" '
                         f'fill="{color}"/>')
            elif frac > 0.0015:
                x1, y1 = cx + R * math.cos(angle), cy + R * math.sin(angle)
                x2, y2 = cx + R * math.cos(a2),    cy + R * math.sin(a2)
                large  = 1 if frac > 0.5 else 0
                piece = (f'<path d="M{cx:.1f} {cy:.1f}L{x1:.1f} {y1:.1f}'
                         f'A{R} {R} 0 {large} 1 {x2:.1f} {y2:.1f}Z" '
                         f'fill="{color}"/>')
            else:
                piece = ""
            if piece:
                p.append(f'<g opacity="0">'
                         f'{fade(0.22 + gi * 0.10 + ri * 0.06)}'
                         + piece + '</g>')
            ly = 36 + ri * 20
            p.append(f'<g opacity="0">{fade(0.24 + gi * 0.10 + ri * 0.05)}'
                     + f'<rect x="{lx:.0f}" y="{ly - 9:.0f}" width="8" height="8" '
                       f'rx="2" fill="{color}"/>'
                     + label(lx + 14, ly, name.lower()[:13], 11, "e-f")
                     + label(gx + colw - 6, ly, f"{frac * 100:.0f}%",
                             11, "m-f", "end")
                     + '</g>')
            angle = a2
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
    """The year as a colored grid — one rounded cell per day, five levels."""
    pad_l, pad_t = LEFT, 44
    weeks        = s["weeks"]
    cell         = 9.0
    step         = cell + 1.6
    H            = int(pad_t + 7 * step + 28)

    # five levels, quiet → loud; level 5 is the accent reserved for the
    # single busiest day so the peak pops in a different hue
    LEVELS_LIGHT = ["#eae5f7", "#cfc3f2", "#a78bfa", "#7c3aed", "#4c1d95"]
    LEVELS_DARK  = ["#1a1430", "#3b2a63", "#7c3aed", "#a78bfa", "#ede9fe"]
    ACCENT       = "#f59e0b"

    def level(v):
        for i, cut in enumerate((0, 2, 5, 9)):
            if v <= cut:
                return i
        return 4

    p = [head(WIDTH, H)]
    p[-1] += ("<style>"
              + "".join(f".lv{i}{{fill:{LEVELS_LIGHT[i]}}}" for i in range(5))
              + f".acc{{fill:{ACCENT}}}"
              + "@media(prefers-color-scheme:dark){"
              + "".join(f".lv{i}{{fill:{LEVELS_DARK[i]}}}" for i in range(5))
              + f".acc{{fill:{ACCENT}}}}}"
              + "</style>")

    bcount, bdate = s.get("busiest_day") or (0, None)
    sub = (f"{fmt(s['total'])} contributions · {s['active']} of "
           f"{sum(len(w) for w in weeks)} days active")
    if bdate:
        sub += f" · busiest day {bcount} on {pretty(bdate)}"
    p.append(f'<g opacity="0">{fade(0.10)}'
             + label(pad_l, 16, "THE YEAR", 9, "m-f",
                     extra=' letter-spacing="1.3"')
             + label(pad_l, 32, sub, 11)
             + '</g>')

    # legend: less … more, real cells instead of characters
    lx = WIDTH - 6
    p.append(f'<g opacity="0">{fade(1.30)}')
    p.append(label(lx - 84, 33, "less", 9, "m-f", "end"))
    for i in range(5):
        p.append(f'<rect x="{lx - 78 + i * 11:.1f}" y="25" width="{cell}" '
                 f'height="{cell}" rx="1.5" class="lv{i}"/>')
    p.append(label(lx - 78 + 5 * 11 + 4, 33, "more", 9, "m-f") + '</g>')

    cid = "yr"
    grid_w = len(weeks) * step
    p.append(f'<clipPath id="{cid}"><rect x="{pad_l}" y="{pad_t - 4}" '
             f'height="{7 * step + 6}" width="0"><animate '
             f'attributeName="width" from="0" to="{grid_w:.1f}" '
             f'begin="0.30s" dur="{REVEAL}s" fill="freeze"/></rect>'
             f'</clipPath>')
    p.append(f'<g clip-path="url(#{cid})">')
    for i, w in enumerate(weeks):
        m = int(w[0]["date"][5:7])
        x = pad_l + i * step
        if i and m != int(weeks[i - 1][0]["date"][5:7]):
            p.append(f'<line x1="{x - 0.8:.1f}" y1="{pad_t - 4}" '
                     f'x2="{x - 0.8:.1f}" y2="{pad_t + 7 * step - 2}" '
                     f'class="u-s" stroke-width="0.5" opacity="0.5"/>')
        for day in w:
            r = day.get("weekday")
            if r is None:
                continue
            lv  = level(day["contributionCount"])
            cls = "acc" if bdate and day["date"] == bdate else f"lv{lv}"
            y   = pad_t + r * step
            p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell}" '
                     f'height="{cell}" rx="1.5" class="{cls}"/>')
    p.append('</g>')

    for r, lab in ((1, "mon"), (3, "wed"), (5, "fri")):
        p.append(label(pad_l - 7, pad_t + r * step + cell - 1, lab, 9, "m-f",
                       "end"))

    last_m, last_x = None, -999.0
    base_y = pad_t + 7 * step + 14
    for i, w in enumerate(weeks):
        m = int(w[0]["date"][5:7])
        x = pad_l + i * step
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


def remove_waka_section(path):
    """Remove stale WakaTime output if an external updater inserts it."""
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        old = f.read()
    cleaned = re.sub(
        r"\n*<!--START_SECTION:waka-->.*?<!--END_SECTION:waka-->\.?\s*",
        "\n",
        old,
        flags=re.DOTALL,
    )
    if cleaned == old:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(cleaned)
    return True


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set")
    login   = os.environ.get("GH_LOGIN", "0nsku")
    out_dir = os.environ.get("OUT_DIR", ".")

    user = fetch(login, token)
    s = summarise(user)
    s = apply_exclusions(s, login, token)
    cache_path = os.path.join(out_dir, "profile-data.json")
    if (user.get("_viewerLogin") or "").lower() == login.lower() and user.get("_privateRepoCount"):
        print(f"scanning {user['_privateRepoCount']} private repositories...")
        private = private_activity(login, token)
        if not s["total"]:
            s = private
        else:
            # the calendar already covers private activity; the repo scan still
            # improves the language charts, which otherwise see public repos only.
            # Its per-repo commit count is also the only source that covers
            # private repos — GraphQL's commit totals are public-only, so the
            # card uses the scan's count (both already exclude EXCLUDED_REPOS).
            s["by_size"], s["by_repo"] = private["by_size"], private["by_repo"]
            s["lang_source"] = "all-repos"
            s["commits_ly"] = private["commits_ly"]
            s["commits_cy"] = private["commits_cy"]
    elif not s["total"]:
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
    if remove_waka_section(os.path.join(out_dir, "README.md")):
        changed.append("README.md")
    print(f"{s['total']} commits, {s['active']} active days, "
          f"best week {s['best_week']}, current streak "
          f"{s['current']['length']}, longest {s['longest']['length']}")
    print("languages (recent-weighted): "
          + ", ".join(f"{n} {v}" for n, v in s["by_size"]))
    print(f"preserved profile views: {view_count}")
    print("updated: " + (", ".join(sorted(changed)) if changed else "nothing"))


if __name__ == "__main__":
    main()
