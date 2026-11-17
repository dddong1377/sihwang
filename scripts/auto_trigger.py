"""GitHub 예약 작업에서 실행하는 자동 실행 도우미.

  python scripts/auto_trigger.py check        지금 실행할 작업이 있는지 확인 (GITHUB_OUTPUT 에 due 기록)
  python scripts/auto_trigger.py run pre      앱을 열어 장전 시황을 자동 실행하고 결과를 기다림

필요한 환경변수(GitHub Secrets):
  GIST_TOKEN, GIST_ID   앱이 시황글·설정을 저장하는 Gist
  APP_URL               앱 주소 (예: https://xxxx.streamlit.app)
  APP_PASSWORD          앱 로그인 비밀번호
선택: PAGE_PATH_PRE / PAGE_PATH_AFT / PAGE_PATH_WEEK  (왼쪽 메뉴 각 페이지의 주소 끝부분)
"""

import datetime
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

import requests

KST = ZoneInfo("Asia/Seoul")
GIST_FILE = "mtnw_archive.json"

DEFAULT_TIMES = {"pre": "08:00", "aft": "14:30", "week": "15:40"}

# 설정 시각으로부터 이 시간 안에만 실행 (너무 늦은 보충 실행 방지)
WINDOW_HOURS = {"pre": 3, "aft": 3, "week": 4}

RUNNING_GUARD_MINUTES = 30

# 실패해도 하루에 최대 이 횟수까지만 시도 (토큰 낭비 방지)
MAX_ATTEMPTS = 2
WAIT_MINUTES = {"pre": 25, "aft": 25, "week": 15}

DEFAULT_PATHS = {"pre": "premarket", "aft": "afternoon", "week": "weekly"}


def read_gist():
    token = os.environ["GIST_TOKEN"]
    gist_id = os.environ["GIST_ID"]

    response = requests.get(
        f"https://api.github.com/gists/{gist_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=20,
    )
    response.raise_for_status()

    file = response.json().get("files", {}).get(GIST_FILE)
    content = (file or {}).get("content", "")

    data = json.loads(content) if content.strip() else {}

    return data if isinstance(data, dict) else {}


def write_gist(data):
    token = os.environ["GIST_TOKEN"]
    gist_id = os.environ["GIST_ID"]

    response = requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"files": {GIST_FILE: {"content": json.dumps(data, ensure_ascii=False)}}},
        timeout=20,
    )
    response.raise_for_status()


def record_failure(kind, message):
    """앱에 접속조차 못 한 경우에도 실패를 기록해 같은 날 무한 재시도를 막습니다."""

    try:
        data = read_gist()
        today = datetime.datetime.now(KST).date().isoformat()

        runs = data.setdefault("_runs", {}).setdefault(today, {})
        previous = runs.get(kind)
        attempts = int(previous.get("attempts", 0)) if isinstance(previous, dict) else 0

        # 앱이 시작 기록을 남겼다면 이미 시도 횟수에 포함돼 있음
        if not (isinstance(previous, dict) and previous.get("ok") is None):
            attempts += 1

        runs[kind] = {
            "ok": False,
            "message": str(message)[:300],
            "at": datetime.datetime.now(KST).isoformat(timespec="seconds"),
            "attempts": attempts,
        }

        write_gist(data)

    except Exception as error:
        print(f"실패 기록을 저장하지 못했습니다: {error}", flush=True)


def is_trading_day(day):
    """평일이고 한국 공휴일·근로자의 날·연말 휴장일이 아니면 True."""

    if day.weekday() >= 5:
        return False

    if (day.month, day.day) in ((5, 1), (12, 31)):
        return False

    try:
        import holidays

        return day not in holidays.country_holidays("KR", years=day.year)

    except Exception:
        return True


def due_kinds(data, now):
    config = data.get("_config") if isinstance(data.get("_config"), dict) else {}

    if not config.get("enabled"):
        return []

    times = dict(DEFAULT_TIMES)

    if isinstance(config.get("times"), dict):
        for kind, value in config["times"].items():
            if kind in times:
                times[kind] = str(value)

    today = now.date()

    if not is_trading_day(today):
        return []

    runs = (data.get("_runs") or {}).get(today.isoformat(), {})

    due = []

    for kind in ("pre", "aft", "week"):
        if kind == "week" and today.weekday() != 4:
            continue

        try:
            hour, minute = times[kind].split(":")
            start = datetime.datetime.combine(
                today,
                datetime.time(int(hour), int(minute)),
                tzinfo=KST,
            )
        except (ValueError, TypeError):
            continue

        if not (start <= now < start + datetime.timedelta(hours=WINDOW_HOURS[kind])):
            continue

        run = runs.get(kind) if isinstance(runs, dict) else None

        if isinstance(run, dict):
            if run.get("ok") is True:
                continue

            if int(run.get("attempts", 0)) >= MAX_ATTEMPTS and run.get("ok") is not None:
                continue

            # 실행 중으로 보이면 건너뜀
            if run.get("ok") is None:
                try:
                    started = datetime.datetime.fromisoformat(run.get("at"))
                    age = (now - started).total_seconds() / 60

                    if age < RUNNING_GUARD_MINUTES:
                        continue
                except (ValueError, TypeError):
                    pass

        due.append(kind)

    return due


def cmd_check():
    now = datetime.datetime.now(KST)
    due = due_kinds(read_gist(), now)

    print(f"[{now:%Y-%m-%d %H:%M}] 실행할 작업: {due or '없음'}")

    output = os.environ.get("GITHUB_OUTPUT")

    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"due={' '.join(due)}\n")


def find_in_frames(page, getter, seconds):
    """Streamlit Cloud 는 앱을 iframe 안에 넣으므로 모든 프레임에서 찾습니다."""

    end = time.time() + seconds

    while time.time() < end:
        for frame in page.frames:
            try:
                locator = getter(frame)

                if locator.count() > 0 and locator.first.is_visible():
                    return locator.first
            except Exception:
                pass

        time.sleep(2)

    return None


def debug_dump(page):
    try:
        page.screenshot(path="debug_login.png", full_page=True)
    except Exception:
        pass

    for frame in page.frames:
        try:
            text = frame.locator("body").inner_text(timeout=3000)
        except Exception:
            text = "(읽지 못함)"

        print(f"[프레임] {frame.url[:120]}", flush=True)
        print("   " + " ".join(text.split())[:400], flush=True)


def run_status(kind):
    today = datetime.datetime.now(KST).date().isoformat()
    runs = (read_gist().get("_runs") or {}).get(today, {})

    run = runs.get(kind) if isinstance(runs, dict) else None

    return run if isinstance(run, dict) else None


def cmd_run(kind):
    from playwright.sync_api import sync_playwright

    base = os.environ["APP_URL"].rstrip("/")
    password = os.environ["APP_PASSWORD"]
    path = os.environ.get(f"PAGE_PATH_{kind.upper()}") or DEFAULT_PATHS[kind]

    url = f"{base}/{path}?auto={kind}"

    deadline = time.time() + WAIT_MINUTES[kind] * 60
    launched = datetime.datetime.now(KST)

    print(f"열기: {base}/{path}?auto={kind}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)

        # 잠든 앱 깨우기
        wake = find_in_frames(
            page,
            lambda f: f.get_by_role("button", name="Yes, get this app back up!"),
            20,
        )

        if wake is not None:
            print("잠든 앱을 깨우는 중...", flush=True)
            wake.click()

        # 로그인 칸 찾아 입력 (앱이 깨어나는 중이면 칸이 잠겨 있어 재시도·새로고침)
        logged_in = False
        login_deadline = time.time() + 300
        reloads = 0

        while time.time() < login_deadline and not logged_in:
            # 잠든 앱 깨우기 버튼이 뒤늦게 뜰 수 있어 매 반복마다 확인
            wake = find_in_frames(
                page,
                lambda f: f.get_by_role("button", name="Yes, get this app back up!"),
                3,
            )

            if wake is not None:
                print("잠든 앱을 깨우는 중...", flush=True)

                try:
                    wake.click(timeout=10_000)
                except Exception:
                    pass

                time.sleep(20)

            box = find_in_frames(page, lambda f: f.get_by_label("비밀번호"), 60)

            if box is None:
                if reloads >= 3:
                    break
                reloads += 1
                page.reload(wait_until="domcontentloaded", timeout=120_000)
                continue

            try:
                box.click(timeout=15_000)
                box.fill(password, timeout=15_000)
                box.press("Enter", timeout=10_000)
                logged_in = True
            except Exception as error:
                print(f"로그인 칸 입력 실패(재시도): {str(error)[:120]}", flush=True)
                time.sleep(10)

                if reloads < 3:
                    reloads += 1
                    page.reload(wait_until="domcontentloaded", timeout=120_000)

        if not logged_in:
            print("로그인하지 못했습니다. 화면 상태:", flush=True)
            debug_dump(page)
            browser.close()
            record_failure(kind, "자동 실행: 앱 로그인에 실패했습니다.")
            sys.exit(1)

        print("로그인 입력 완료. 실행 결과를 기다립니다.", flush=True)

        result = None

        while time.time() < deadline:
            time.sleep(20)

            run = run_status(kind)

            # 이번 실행 이후에 남겨진 기록만 결과로 인정 (이전 시도의 기록 무시)
            try:
                recorded = datetime.datetime.fromisoformat(run.get("at")) if run else None
            except (TypeError, ValueError):
                recorded = None

            if run and run.get("ok") is not None and recorded and recorded >= launched:
                result = run
                break

        browser.close()

    if result is None:
        print("제한 시간 안에 결과가 기록되지 않았습니다.", flush=True)
        record_failure(kind, "자동 실행: 제한 시간 안에 결과가 기록되지 않았습니다.")
        sys.exit(1)

    print(("성공: " if result.get("ok") else "실패: ") + str(result.get("message", "")))

    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""

    if action == "check":
        cmd_check()
    elif action == "run" and len(sys.argv) > 2 and sys.argv[2] in DEFAULT_TIMES:
        try:
            cmd_run(sys.argv[2])
        except SystemExit:
            raise
        except Exception as error:
            print(f"예상하지 못한 오류: {error}", flush=True)
            record_failure(sys.argv[2], f"자동 실행 오류: {str(error)[:200]}")
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(2)
