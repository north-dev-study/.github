import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import requests


ORG = "north-dev-study"

USERS = [
    "yerinaLee",
    "FireCurry",
    "yj2695",
]

# 저장소 루트를 기준으로 SVG가 생성됩니다.
OUTPUT_DIR = Path("profile/contributions")

# 기본 브랜치의 커밋만 집계합니다.
# Fork/보관 저장소를 포함하려면 아래 값을 True로 변경하세요.
INCLUDE_FORKS = False
INCLUDE_ARCHIVED = False

TOKEN = os.environ["GITHUB_TOKEN"]

HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": f"{ORG}-contribution-graph",
}

API_ROOT = "https://api.github.com"

# GitHub 잔디와 비슷한 5단계 색상입니다.
LIGHT_COLORS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]
DARK_COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]

CELL_SIZE = 11
CELL_GAP = 3
STEP = CELL_SIZE + CELL_GAP
LEFT_MARGIN = 34
TOP_MARGIN = 38
BOTTOM_MARGIN = 30
RIGHT_MARGIN = 12
WEEKS = 53
DAYS = 7


class GitHubAPIError(RuntimeError):
    """GitHub API 호출 실패를 사람이 읽기 쉬운 메시지로 표시합니다."""


def github_get(url: str, params: dict[str, Any] | None = None) -> requests.Response:
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=30,
    )

    # 커밋이 한 번도 없는 빈 저장소는 409를 반환할 수 있습니다.
    if response.status_code == 409:
        return response

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        remaining = response.headers.get("X-RateLimit-Remaining", "unknown")
        reset = response.headers.get("X-RateLimit-Reset", "unknown")
        try:
            message = response.json().get("message", response.text)
        except ValueError:
            message = response.text

        raise GitHubAPIError(
            f"GitHub API 요청 실패: {response.status_code} {message} "
            f"(remaining={remaining}, reset={reset})"
        ) from exc

    return response


def get_org_repositories() -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    page = 1

    while True:
        response = github_get(
            f"{API_ROOT}/orgs/{ORG}/repos",
            params={
                "type": "all",
                "sort": "full_name",
                "per_page": 100,
                "page": page,
            },
        )
        items = response.json()

        if not items:
            break

        for repo in items:
            if not INCLUDE_FORKS and repo.get("fork"):
                continue
            if not INCLUDE_ARCHIVED and repo.get("archived"):
                continue
            repositories.append(repo)

        if len(items) < 100:
            break
        page += 1

    return repositories


def get_user_commits(
    repository: dict[str, Any],
    username: str,
    since: str,
    until: str,
) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    page = 1

    while True:
        response = github_get(
            f"{API_ROOT}/repos/{ORG}/{repository['name']}/commits",
            params={
                # sha를 생략하면 저장소의 기본 브랜치를 조회합니다.
                "author": username,
                "since": since,
                "until": until,
                "per_page": 100,
                "page": page,
            },
        )

        if response.status_code == 409:
            return []

        items = response.json()
        if not items:
            break

        commits.extend(items)

        if len(items) < 100:
            break
        page += 1

    return commits


def calendar_range(today: date) -> tuple[date, date]:
    """GitHub처럼 일요일부터 토요일까지 53주를 표시할 날짜 범위를 계산합니다."""
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    start = last_sunday - timedelta(weeks=WEEKS - 1)
    end = start + timedelta(days=WEEKS * DAYS - 1)
    return start, end


def count_commits_by_date(
    username: str,
    repositories: list[dict[str, Any]],
    start: date,
    end: date,
) -> Counter[str]:
    since = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    until = datetime.combine(
        end,
        datetime.max.time(),
        tzinfo=timezone.utc,
    ).isoformat()

    counts: Counter[str] = Counter()

    for repository in repositories:
        repo_name = repository["name"]
        print(f"  - {repo_name} 조회 중...")

        commits = get_user_commits(
            repository=repository,
            username=username,
            since=since,
            until=until,
        )

        # 같은 저장소의 같은 커밋이 페이지 처리 등으로 중복되는 상황을 방지합니다.
        seen_shas: set[str] = set()
        for commit in commits:
            sha = commit.get("sha")
            if sha and sha in seen_shas:
                continue
            if sha:
                seen_shas.add(sha)

            commit_info = commit.get("commit", {})
            author_info = commit_info.get("author") or commit_info.get("committer") or {}
            committed_at = author_info.get("date")
            if committed_at:
                counts[committed_at[:10]] += 1

    return counts


def contribution_level(count: int, max_count: int) -> int:
    """날짜별 커밋 수를 0~4 단계로 변환합니다."""
    if count <= 0:
        return 0
    if max_count <= 1:
        return 4

    ratio = count / max_count
    if ratio <= 0.25:
        return 1
    if ratio <= 0.50:
        return 2
    if ratio <= 0.75:
        return 3
    return 4


def month_labels(start: date) -> list[tuple[int, str]]:
    labels: list[tuple[int, str]] = []
    previous_month: int | None = None

    for week in range(WEEKS):
        current = start + timedelta(weeks=week)
        # 주간 칸이 대부분 해당 월에 속하도록 수요일을 기준으로 월을 표시합니다.
        middle = current + timedelta(days=3)
        if middle.month != previous_month:
            labels.append((week, middle.strftime("%b")))
            previous_month = middle.month

    return labels


def build_svg(username: str, counts: Counter[str], start: date, end: date) -> str:
    width = LEFT_MARGIN + WEEKS * STEP - CELL_GAP + RIGHT_MARGIN
    height = TOP_MARGIN + DAYS * STEP - CELL_GAP + BOTTOM_MARGIN
    total = sum(counts.values())
    max_count = max(counts.values(), default=0)

    cells: list[str] = []
    for week in range(WEEKS):
        for weekday in range(DAYS):
            current = start + timedelta(weeks=week, days=weekday)
            count = counts.get(current.isoformat(), 0)
            level = contribution_level(count, max_count)
            x = LEFT_MARGIN + week * STEP
            y = TOP_MARGIN + weekday * STEP

            cells.append(
                f'<rect class="day level-{level}" x="{x}" y="{y}" '
                f'width="{CELL_SIZE}" height="{CELL_SIZE}" rx="2">'
                f'<title>{escape(current.isoformat())}: {count} commit(s)</title>'
                "</rect>"
            )

    months = "".join(
        f'<text class="label month" x="{LEFT_MARGIN + week * STEP}" y="28">'
        f"{label}</text>"
        for week, label in month_labels(start)
    )

    weekday_labels = "".join(
        [
            f'<text class="label" x="0" y="{TOP_MARGIN + 1 * STEP + 9}">Mon</text>',
            f'<text class="label" x="0" y="{TOP_MARGIN + 3 * STEP + 9}">Wed</text>',
            f'<text class="label" x="0" y="{TOP_MARGIN + 5 * STEP + 9}">Fri</text>',
        ]
    )

    legend_x = width - 5 * STEP - 37
    legend_y = height - 17
    legend = (
        f'<text class="label" x="{legend_x - 30}" y="{legend_y + 9}">Less</text>'
        + "".join(
            f'<rect class="day level-{level}" x="{legend_x + level * STEP}" '
            f'y="{legend_y}" width="{CELL_SIZE}" height="{CELL_SIZE}" rx="2" />'
            for level in range(5)
        )
        + f'<text class="label" x="{legend_x + 5 * STEP + 2}" y="{legend_y + 9}">More</text>'
    )

    title = f"{username}: {total} commits in {ORG}"
    description = (
        f"Commit activity for GitHub user {username} in organization {ORG}, "
        f"from {start.isoformat()} to {end.isoformat()}."
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">{escape(title)}</title>
  <desc id="desc">{escape(description)}</desc>
  <style>
    .label {{ font: 10px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #57606a; }}
    .month {{ font-size: 10px; }}
    .level-0 {{ fill: {LIGHT_COLORS[0]}; }}
    .level-1 {{ fill: {LIGHT_COLORS[1]}; }}
    .level-2 {{ fill: {LIGHT_COLORS[2]}; }}
    .level-3 {{ fill: {LIGHT_COLORS[3]}; }}
    .level-4 {{ fill: {LIGHT_COLORS[4]}; }}
    @media (prefers-color-scheme: dark) {{
      .label {{ fill: #8c959f; }}
      .level-0 {{ fill: {DARK_COLORS[0]}; }}
      .level-1 {{ fill: {DARK_COLORS[1]}; }}
      .level-2 {{ fill: {DARK_COLORS[2]}; }}
      .level-3 {{ fill: {DARK_COLORS[3]}; }}
      .level-4 {{ fill: {DARK_COLORS[4]}; }}
    }}
  </style>
  {months}
  {weekday_labels}
  {''.join(cells)}
  {legend}
</svg>
'''


def write_svg(username: str, counts: Counter[str], start: date, end: date) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{username}.svg"
    output_path.write_text(
        build_svg(username=username, counts=counts, start=start, end=end),
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    today = datetime.now(timezone.utc).date()
    start, end = calendar_range(today)

    print(f"Organization: {ORG}")
    print(f"조회 기간: {start} ~ {end}")

    repositories = get_org_repositories()
    print(f"대상 저장소: {len(repositories)}개")

    for username in USERS:
        print(f"\n[{username}]")
        counts = count_commits_by_date(
            username=username,
            repositories=repositories,
            start=start,
            end=end,
        )
        output_path = write_svg(username, counts, start, end)

        print(f"총 커밋 수: {sum(counts.values())}")
        print(f"SVG 생성: {output_path}")


if __name__ == "__main__":
    main()
