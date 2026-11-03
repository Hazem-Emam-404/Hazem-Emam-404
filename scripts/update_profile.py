"""Keeps the GitHub profile README fresh.

1. Renders the project sections of README.md from projects.json, plus any of your
   repos that carry the section's topic (for example `ai-project`).
2. Generates self-hosted stats cards (assets/stats.svg and assets/languages.svg)
   so the profile never depends on a third-party stats service.

Runs in the GitHub Action; you can also run it locally: python scripts/update_profile.py
"""
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
USER = os.environ.get("GH_USER", "Hazem-Emam-404")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
INCLUDE_FORKS = os.environ.get("INCLUDE_FORKS", "1") == "1"  # count forked faculty/team repos in the language card

REPO_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s#?]+)")
FONT = "'Segoe UI',Helvetica,Arial,sans-serif"
LANG_COLORS = {
    "Python": "#3776AB", "Java": "#b07219", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "Dart": "#00B4AB", "C++": "#f34b7d", "C": "#8a8a8a", "Jupyter Notebook": "#DA5B0B",
    "HTML": "#e34c26", "CSS": "#563d7c", "Assembly": "#6E4C13", "Kotlin": "#A97BFF",
    "Swift": "#F05138", "Shell": "#89e051", "Dockerfile": "#384d54", "C#": "#178600",
    "SCSS": "#c6538c", "Makefile": "#427819", "CMake": "#DA3434",
}


# ---------------------------------------------------------------- GitHub API
def api(path):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "profile-readme-updater"},
    )
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except Exception as exc:  # network down, rate limit, repo missing...
        print(f"warning: {path}: {exc}", file=sys.stderr)
        return None


def fetch_repos():
    repos, page = [], 1
    while True:
        batch = api(f"/users/{USER}/repos?per_page=100&page={page}&sort=updated")
        if batch is None:
            return None if page == 1 else repos
        if not batch:
            return repos
        repos.extend(batch)
        page += 1


# ------------------------------------------------------------ project cards
def parse_entry(entry):
    if isinstance(entry, str):
        entry = {"url": entry}
    match = REPO_RE.search(entry["url"])
    if not match:
        raise ValueError(f"Not a GitHub repo link: {entry['url']}")
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    entry = dict(entry)
    entry.update(owner=owner, repo=repo, full=f"{owner}/{repo}", url=f"https://github.com/{owner}/{repo}")
    return entry


def badge(label, color):
    text = quote(label.replace("-", "--").replace("_", "__").replace(" ", "_"), safe="")
    return f"![{label}](https://img.shields.io/badge/{text}-{color}?style=flat-square)"


def render_card(entry, info, accent):
    name = entry.get("title") or info.get("name") or entry["repo"]
    emoji = entry.get("emoji", "🔹")
    language = info.get("language")
    desc = (
        entry.get("description")
        or (info.get("description") or "").strip()
        or (f"A {language} project from my GitHub." if language else "A project from my GitHub.")
    )
    stack = entry.get("stack") or ([language] if language else [])
    meta = " ".join(badge(item, accent) for item in stack)
    stars = info.get("stargazers_count", 0)
    if stars:
        meta += f" &nbsp; ⭐ **{stars}**"
    lines = [f"> #### {emoji} [{name}]({entry['url']})", f"> {desc}"]
    if meta:
        lines += [">", f"> {meta}"]
    return "\n".join(lines)


def render_section(section, by_full, all_repos):
    accent = section.get("accent", "7c3aed")
    cards, seen = [], set()
    for raw in section.get("projects", []):
        entry = parse_entry(raw)
        seen.add(entry["full"].lower())
        info = by_full.get(entry["full"].lower()) or api(f"/repos/{entry['full']}") or {}
        cards.append(render_card(entry, info, accent))
    topics = set(section.get("topics", []))
    extras = [
        r for r in all_repos
        if r["full_name"].lower() not in seen
        and r["name"].lower() != USER.lower()
        and topics & set(r.get("topics") or [])
    ]
    extras.sort(key=lambda r: (-r["stargazers_count"], r["name"].lower()))
    for repo in extras:
        entry = {"url": repo["html_url"], "repo": repo["name"]}
        cards.append(render_card(entry, repo, accent))
    if not cards:
        return "*Coming soon.*"
    return "\n\n".join(cards)


def inject(text, marker, content):
    pattern = re.compile(rf"(<!-- {marker}:START -->)(.*?)(<!-- {marker}:END -->)", re.S)
    if not pattern.search(text):
        print(f"warning: markers for {marker} not found in README.md", file=sys.stderr)
        return text
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", text)


# ------------------------------------------------------------ stats cards
def svg_card(width, height, title, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#1b1340"/></linearGradient>
  <linearGradient id="ac" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#a78bfa"/><stop offset="1" stop-color="#22d3ee"/></linearGradient>
  <clipPath id="bar"><rect x="25" y="56" width="{width - 50}" height="12" rx="6"/></clipPath>
</defs>
<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="14" fill="url(#bg)" stroke="#30363d"/>
<text x="25" y="36" font-family="{FONT}" font-size="18" font-weight="700" fill="url(#ac)">{html.escape(title)}</text>
{body}
</svg>
"""


def stats_svg(user, repos):
    mine = [r for r in repos if not r["fork"]]
    items = [
        (len(repos), "Public repos"),
        (sum(r["stargazers_count"] for r in mine), "Stars earned"),
        (sum(r["forks_count"] for r in mine), "Forks"),
        (user.get("followers", 0), "Followers"),
        (user.get("following", 0), "Following"),
        ((user.get("created_at") or "----")[:4], "On GitHub since"),
    ]
    body = []
    for i, (value, label) in enumerate(items):
        x, y = 25 + (i % 3) * 160, 78 + (i // 3) * 62
        body.append(f'<text x="{x}" y="{y + 12}" font-family="{FONT}" font-size="28" font-weight="700" fill="#e6edf3">{html.escape(str(value))}</text>')
        body.append(f'<text x="{x}" y="{y + 32}" font-family="{FONT}" font-size="12" fill="#8b949e">{html.escape(label)}</text>')
    return svg_card(495, 195, "GitHub Overview", "\n".join(body))


def lang_color(name):
    if name in LANG_COLORS:
        return LANG_COLORS[name]
    return "#" + hashlib.md5(name.encode()).hexdigest()[:6]


def languages_svg(repos):
    totals = Counter()
    for repo in repos:
        if repo.get("archived") or (repo["fork"] and not INCLUDE_FORKS):
            continue
        for name, size in (api(f"/repos/{repo['full_name']}/languages") or {}).items():
            totals[name] += size
    grand = sum(totals.values())
    if not grand:
        return None
    top = totals.most_common(6)
    shown = sum(size for _, size in top)
    if grand > shown:
        top.append(("Other", grand - shown))
    width, x, segments = 495 - 50, 25, []
    for name, size in top:
        seg = width * size / grand
        color = "#8b949e" if name == "Other" else lang_color(name)
        segments.append(f'<rect x="{x:.2f}" y="56" width="{seg:.2f}" height="12" fill="{color}"/>')
        x += seg
    body = [f'<g clip-path="url(#bar)">{"".join(segments)}</g>']
    for i, (name, size) in enumerate(top):
        cx, cy = 25 + (i % 2) * 230, 96 + (i // 2) * 26
        color = "#8b949e" if name == "Other" else lang_color(name)
        body.append(f'<circle cx="{cx + 5}" cy="{cy}" r="5" fill="{color}"/>')
        body.append(
            f'<text x="{cx + 18}" y="{cy + 4}" font-family="{FONT}" font-size="13" fill="#e6edf3">{html.escape(name)}'
            f'<tspan fill="#8b949e" dx="8">{100 * size / grand:.1f}%</tspan></text>'
        )
    return svg_card(495, 195, "Top Languages", "\n".join(body))


# ------------------------------------------------------------------- main
def main():
    config = json.loads((ROOT / "projects.json").read_text(encoding="utf-8"))
    repos = fetch_repos()
    by_full = {r["full_name"].lower(): r for r in (repos or [])}

    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    for section in config["sections"]:
        text = inject(text, section["marker"], render_section(section, by_full, repos or []))
    readme.write_text(text, encoding="utf-8")
    print("project sections updated")

    if repos is None:
        print("GitHub API unreachable: stats cards left unchanged", file=sys.stderr)
        return
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    user = api(f"/users/{USER}") or {}
    (assets / "stats.svg").write_text(stats_svg(user, repos), encoding="utf-8")
    langs = languages_svg(repos)
    if langs:
        (assets / "languages.svg").write_text(langs, encoding="utf-8")
    print("stats cards updated")


if __name__ == "__main__":
    main()
