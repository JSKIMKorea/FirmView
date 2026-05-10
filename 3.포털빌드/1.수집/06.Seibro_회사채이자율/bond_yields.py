"""
bond_yields.py -- Seibro 채권만기수익률 자동 추출 (일별)

대상: 한국예탁결제원 Seibro > 채권 > 채권만기수익률
URL : https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/bond/BIP_CNTS03030V.xml&menuNo=120

추출 기준: 영업일 기준 매일 1건
저장: cache/bond_yields_{YYYYMMDD}.json (일별 1파일)

수집 대상:
  - 회사채 > 공모무보증 (AAA, AA+, AA, AA-, A+, A, A-, BBB+, BBB, BBB-)
  - 국채 (양곡·외평·재정) - 무위험 기준 비교용

비영업일 처리:
  - 토·일요일은 호출 자체를 건너뜀 (시간 절약)
  - 평일이지만 데이터 없는 날(공휴일·한정 휴장)은 sentinel 파일 저장 → 재시도 안 함

이미 캐시된 일자는 자동 skip - 캐시 파일을 직접 삭제하면 재추출됨.

실행 예시:
  python bond_yields.py                            # 인터랙티브 입력 (기간을 화면에서 직접 입력)
  python bond_yields.py 2026-04-30                 # 단일 일자
  python bond_yields.py 2023-01-01 2023-01-31      # 기간 (1일 간격, 영업일만)
  python bond_yields.py 2024-01 2024-12            # YYYY-MM 입력 시 자동으로 시작=1일 / 끝=말일

인터랙티브 입력 시 시작·종료 일자를 화면에서 입력받음.
엔터만 치면 기본값 사용 (시작=2023-01-01, 종료=오늘).
"""
import asyncio
import calendar
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

# Windows cp949 stdout 인코딩 회피
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "11.회사채만기수익률_Seibro"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
SEIBRO_URL = (
    "https://seibro.or.kr/websquare/control.jsp"
    "?w2xPath=/IPORTAL/user/bond/BIP_CNTS03030V.xml&menuNo=120"
)

# Seibro 표기 → Portal 표기 (AA0 → AA, A0 → A, BBB0 → BBB)
GRADE_MAP = {
    "AAA": "AAA",
    "AA+": "AA+", "AA0": "AA", "AA-": "AA-",
    "A+":  "A+",  "A0":  "A",  "A-":  "A-",
    "BBB+": "BBB+", "BBB0": "BBB", "BBB-": "BBB-",
}

TENORS = ["3M", "6M", "9M", "1Y", "3Y", "5Y", "10Y", "20Y"]


# ── 일자 유틸 ────────────────────────────────────────────────────
def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def generate_daily(start: date, end: date, weekdays_only: bool = True) -> list[date]:
    """[start ~ end] 1일 간격 일자 리스트. weekdays_only=True 면 토/일 제외"""
    out: list[date] = []
    d = start
    while d <= end:
        if (not weekdays_only) or (d.weekday() < 5):  # 0=Mon ~ 4=Fri
            out.append(d)
        d += timedelta(days=1)
    return out


def parse_start(s: str) -> date:
    """시작일 파싱: 'YYYY-MM-DD' 또는 'YYYY-MM' (월 1일로 해석)"""
    parts = s.split("-")
    if len(parts) == 2:
        return date(int(parts[0]), int(parts[1]), 1)
    return date.fromisoformat(s)


def parse_end(s: str) -> date:
    """종료일 파싱: 'YYYY-MM-DD' 또는 'YYYY-MM' (월 말일로 해석)"""
    parts = s.split("-")
    if len(parts) == 2:
        return month_end(int(parts[0]), int(parts[1]))
    return date.fromisoformat(s)


# ── 페이지 조작 ──────────────────────────────────────────────────
async def set_date_and_search(page, target: date) -> bool:
    """기준일 input에 날짜 입력 후 조회 버튼 클릭"""
    target_str = target.strftime("%Y/%m/%d")

    try:
        date_input = page.locator("#ic2_select_input")
        await date_input.click()
        await date_input.fill("")
        await date_input.type(target_str, delay=15)
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.25)

        await page.locator("#group10").click()
        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(1.0)
        return True
    except PWTimeoutError as e:
        print(f"    [타임아웃] {e}")
        return False
    except Exception as e:
        print(f"    [입력/클릭 오류] {e}")
        return False


async def extract_table(page) -> list[list[str]]:
    """결과 테이블 행 매트릭스 추출 (rowspan 정규화)"""
    return await page.evaluate(
        """() => {
        const tables = Array.from(document.querySelectorAll('table'));
        let target = null;
        for (const t of tables) {
            const txt = t.textContent || '';
            if (txt.includes('신용등급') && txt.includes('3M') && txt.includes('20Y')) {
                target = t; break;
            }
        }
        if (!target) return [];

        const rows = Array.from(target.querySelectorAll('tbody tr'));
        const grid = [];
        const carry = {};
        for (let r = 0; r < rows.length; r++) {
            const cells = Array.from(rows[r].querySelectorAll('th, td'));
            const rowOut = [];
            const occupied = {};
            for (const k of Object.keys(carry)) {
                const c = carry[k];
                if (c.remaining > 0) {
                    occupied[k] = c.text;
                    c.remaining--;
                    if (c.remaining === 0) delete carry[k];
                }
            }
            let physIdx = 0;
            for (let pos = 0; pos < 100; pos++) {
                if (occupied[pos] !== undefined) { rowOut.push(occupied[pos]); continue; }
                if (physIdx >= cells.length) break;
                const cell = cells[physIdx];
                physIdx++;
                const txt = (cell.textContent || '').trim();
                rowOut.push(txt);
                const rs = parseInt(cell.getAttribute('rowspan') || '1', 10);
                if (rs > 1) carry[pos] = { text: txt, remaining: rs - 1 };
            }
            grid.push(rowOut);
        }
        return grid;
        }"""
    )


def parse_table(rows: list[list[str]]) -> dict | None:
    yields: dict[str, list[float]] = {}
    gov: dict[str, float] = {}
    for cells in rows:
        if len(cells) < 9:
            continue
        label = cells[-9]
        vals = cells[-8:]
        m = re.search(r"무보증\s*공모\s*회사채\s*([A-Z+\-0]+)", label)
        if m:
            seibro_grade = m.group(1)
            grade = GRADE_MAP.get(seibro_grade)
            if grade:
                try:
                    yields[grade] = [float(v) for v in vals]
                except ValueError:
                    pass
            continue
        if "양곡" in label and "재정" in label:
            try:
                gov_vals = [float(v) for v in vals]
                gov = dict(zip(TENORS, gov_vals))
            except ValueError:
                pass
    if not yields:
        return None
    return {"yields": yields, "gov": gov}


def write_sentinel(cache_file: Path, target: date, reason: str) -> None:
    """비영업일/데이터없음 - sentinel 파일 (다음 실행 시 재시도 안 하도록)"""
    cache_file.write_text(
        json.dumps({
            "as_of": target.isoformat(),
            "skipped": True,
            "reason": reason,
            "fetched_at": date.today().strftime("%Y%m%d"),
        }, ensure_ascii=False), encoding="utf-8"
    )


async def fetch_one(page, target: date) -> str:
    """단일 일자 처리. 반환: 'saved' / 'cached' / 'skipped' / 'failed'"""
    cache_file = CACHE_DIR / f"bond_yields_{target.strftime('%Y%m%d')}.json"
    if cache_file.exists():
        return "cached"

    if not await set_date_and_search(page, target):
        return "failed"

    rows = await extract_table(page)
    if not rows:
        write_sentinel(cache_file, target, "no_table")
        return "skipped"

    parsed = parse_table(rows)
    if parsed is None:
        write_sentinel(cache_file, target, "no_data")
        return "skipped"

    payload = {
        "as_of": target.isoformat(),
        "tenors": TENORS,
        "yields": parsed["yields"],
        "gov": parsed["gov"],
        "fetched_at": date.today().strftime("%Y%m%d"),
    }
    cache_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return "saved"


# ── 메인 ─────────────────────────────────────────────────────────
def prompt_dates() -> tuple[date, date]:
    """인자 없이 실행 시 시작·종료 일자 인터랙티브 입력
    엔터만 치면 기본값 사용 (시작=2023-01-01, 종료=오늘)
    """
    print("=" * 60)
    print("  Seibro 채권만기수익률 추출 - 기간 설정")
    print("=" * 60)
    print("  형식: YYYY-MM-DD  또는  YYYY-MM (월 단위)")
    print()

    default_start = "2023-01-01"
    default_end = date.today().isoformat()

    while True:
        s = input(f"  시작 일자 [기본 {default_start}]: ").strip() or default_start
        try:
            start = parse_start(s)
            break
        except (ValueError, TypeError):
            print(f"    × 잘못된 형식: {s}")

    while True:
        e = input(f"  종료 일자 [기본 {default_end}]: ").strip() or default_end
        try:
            end = parse_end(e)
            break
        except (ValueError, TypeError):
            print(f"    × 잘못된 형식: {e}")

    print()
    print(f"  → 추출 범위: {start} ~ {end}")
    print("=" * 60)
    print()
    return start, end


async def main():
    args = sys.argv[1:]

    if len(args) == 0:
        # 인자 없으면 인터랙티브 입력 (사용자가 직접 기간 설정)
        try:
            start, end = prompt_dates()
        except (KeyboardInterrupt, EOFError):
            print("\n취소됨.")
            return
    elif len(args) == 1:
        d = parse_end(args[0])  # 단일 일자
        start = end = d
    elif len(args) == 2:
        start = parse_start(args[0])
        end = parse_end(args[1])
    else:
        print("사용법:")
        print("  python bond_yields.py                          # 인터랙티브 (기간 입력 받음)")
        print("  python bond_yields.py 2026-05-04               # 단일 일자")
        print("  python bond_yields.py 2023-01-01 2026-05-04    # 기간 (1일 간격)")
        return

    if start > end:
        print(f"오류: 시작({start}) > 끝({end})")
        return

    targets = generate_daily(start, end, weekdays_only=True)
    todo = [
        d for d in targets
        if not (CACHE_DIR / f"bond_yields_{d.strftime('%Y%m%d')}.json").exists()
    ]
    print(f"기간: {start} ~ {end}  (영업일 {len(targets)}개 / 신규 추출 {len(todo)}개)")
    if not todo:
        print("스킵 - 모두 캐시 존재")
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 900},
            locale="ko-KR",
        )
        page = await context.new_page()
        page.set_default_timeout(20000)

        print(f"Seibro 페이지 진입: {SEIBRO_URL}")
        await page.goto(SEIBRO_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        saved, skipped, failed = 0, 0, 0
        for i, d in enumerate(todo, 1):
            r = await fetch_one(page, d)
            if r == "saved":
                saved += 1
                if i % 10 == 0 or i == len(todo):
                    print(f"  [{i}/{len(todo)}] {d} ✓ 저장 (누적 저장: {saved}, skip: {skipped}, 실패: {failed})")
            elif r == "skipped":
                skipped += 1
            elif r == "failed":
                failed += 1
                print(f"  [{i}/{len(todo)}] {d} ✗ 실패")
            await asyncio.sleep(0.3)

        await browser.close()
        print(f"\n완료 - 저장 {saved}개 / 비영업일·데이터없음 {skipped}개 / 실패 {failed}개")


if __name__ == "__main__":
    asyncio.run(main())
