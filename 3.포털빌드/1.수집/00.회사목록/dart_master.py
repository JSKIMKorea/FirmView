"""
dart_master.py - 수집 대상 기업 유니버스 생성

대상 = A001(사업보고서) 전체 ∪ Retain Viewer 전체
  - A001: DART list.json 페이지네이션 (최근 2개 공시연도)
  - Retain Viewer: 1.회사명정리/output/company_master.json
  - 합집합: corp_code 기준 중복 제거

각 기업 항목:
  corp_code, corp_name, official_name, candidate_name, stock_code, corp_cls
  is_a001 (bool)          : A001 공시 기업 여부
  is_retain_viewer (bool) : PwC Retain Viewer 대상 여부
  has_f001 (bool)         : F001(감사보고서) 공시 존재 여부 (A001 미제출 기업 한정 스캔)
  dart_data_level (str)   : 수집 가능 데이터 수준
    "a001"  → A001 기반 풀데이터 (재무·감사의견·주주현황·공시 등)
    "f001"  → F001 기반 부분 데이터 (재무·감사의견·기본정보)
    "basic" → 기본정보만 (company.json + 뉴스, DART 보고서 없음)
    "none"  → corp_code 없음, DART 데이터 전혀 없음

corp_code == "" 항목: Retain Viewer에는 있으나 DART 미등록
  → 수집 스킵, 포털 검색 목록에는 표시 (dart_data_level="none")

출력: cache/00.회사목록/company_master_full.json
재수집: python dart_master.py --force
"""

import asyncio
import json
import os
import sys
import time
import warnings
from datetime import date
from pathlib import Path

import httpx

warnings.filterwarnings("ignore")


def _setup_windows_proxy() -> None:
    """Windows 시스템 프록시를 환경변수로 등록 (httpx가 자동 상속)"""
    if "HTTPS_PROXY" in os.environ or "HTTP_PROXY" in os.environ:
        return
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if not enabled:
                return
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
            if "=" in server:
                mapping = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
                server = mapping.get("https", mapping.get("http", server))
            if not server.startswith("http"):
                server = f"http://{server}"
            os.environ["HTTP_PROXY"]  = server
            os.environ["HTTPS_PROXY"] = server
            print(f"  시스템 프록시 감지: {server}")
    except Exception:
        pass

ROOT        = Path(__file__).parent.parent.parent.parent
CRED        = json.loads((ROOT / "credentials.json").read_text(encoding="utf-8"))
DART_KEY    = CRED["dart"]["api_key"]
CACHE_ROOT  = Path(__file__).parent.parent.parent / "cache"
OUT_DIR     = CACHE_ROOT / "00.회사목록"
ORIG_MASTER = ROOT / "1.회사명정리/output/company_master.json"

TODAY      = date.today()
CACHE_DAYS = 30

SEM      = asyncio.Semaphore(5)   # A001 페이지 수집용
F001_SEM = asyncio.Semaphore(3)   # F001 존재 여부 스캔용 - 10 이상이면 ConnectionReset(10054)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}


# ── 유틸 ────────────────────────────────────────────────────────────────────

def _cache_age_days(path: Path) -> float | None:
    if not path.exists():
        return None
    return (time.time() - path.stat().st_mtime) / 86400


# ── DART 연결 진단 ───────────────────────────────────────────────────────────

def _diagnose() -> bool:
    """DART API 연결 및 A001 조회 가능 여부 확인 (동기, 최대 3회 재시도)"""
    print("\n--- DART API 연결 진단 ---")
    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key":        DART_KEY,
        "bgn_de":           f"{TODAY.year - 1}0101",
        "end_de":           f"{TODAY.year - 1}0331",
        "pblntf_ty":        "A",
        "pblntf_detail_ty": "A001",
        "page_no":          1,
        "page_count":       5,
    }
    for attempt in range(1, 4):
        try:
            with httpx.Client(verify=False, timeout=20, headers=_HEADERS) as c:
                r = c.get(url, params=params)
            d = r.json()
            status      = d.get("status", "?")
            total_count = d.get("total_count", "?")
            total_page  = d.get("total_page",  "?")
            msg         = d.get("message", "")[:60]
            print(f"  list.json A001  HTTP {r.status_code} / "
                  f"status={status}  total_count={total_count}  "
                  f"total_page={total_page}  msg={msg!r}")
            if status == "000":
                print()
                return True
            elif status == "010":
                print("  → API 키 오류. credentials.json → dart.api_key 확인 필요.")
            elif status == "020":
                print("  → 일일 호출 한도 초과 (10,000건/일).")
            print()
            return False
        except Exception as e:
            print(f"  연결 실패 (시도 {attempt}/3): {type(e).__name__}: {e!r}")
            if attempt < 3:
                wait = 5 * attempt
                print(f"  {wait}초 후 재시도...")
                time.sleep(wait)
    print()
    return False


# ── A001 수집 ────────────────────────────────────────────────────────────────

async def _fetch_page(
    client: httpx.AsyncClient,
    bgn_de: str,
    end_de: str,
    page_no: int,
) -> tuple[list, int, int]:
    """list.json 단일 페이지. (items, total_pages, total_count) 반환"""
    for attempt in range(1, 4):
        try:
            async with SEM:
                r = await client.get(
                    "https://opendart.fss.or.kr/api/list.json",
                    params={
                        "crtfc_key":          DART_KEY,
                        "bgn_de":             bgn_de,
                        "end_de":             end_de,
                        "pblntf_ty":          "A",
                        "pblntf_detail_ty":   "A001",
                        "page_no":            page_no,
                        "page_count":         100,
                    },
                    timeout=30,
                )
            data = r.json()
            status = data.get("status", "")
            if status == "000":
                return (
                    data.get("list", []),
                    int(data.get("total_page", 1)),
                    int(data.get("total_count", 0)),
                )
            if status in ("013", "020"):
                return [], 0, 0
            return [], 0, 0
        except Exception as e:
            if attempt < 3:
                await asyncio.sleep(2 * attempt)
            else:
                print(f"    [오류] 페이지 {page_no}: {type(e).__name__}: {e!r}")
                return [], 0, 0
    return [], 0, 0


async def _fetch_quarter(bgn_de: str, end_de: str) -> list[dict]:
    """단일 분기(3개월 이내) A001 전체 수집 → 항목 리스트"""
    # 1페이지로 total_pages 파악
    async with httpx.AsyncClient(verify=False, headers=_HEADERS) as client:
        first_items, total_pages, total_count = await _fetch_page(
            client, bgn_de, end_de, 1
        )

    if not total_pages:
        return []

    label = f"{bgn_de[:4]}년 {bgn_de[4:6]}~{end_de[4:6]}월"
    print(f"    {label}: {total_count:,}건 / {total_pages}페이지", end="", flush=True)

    items = list(first_items)

    # 나머지 페이지 병렬 수집 (50페이지 배치)
    async with httpx.AsyncClient(verify=False, headers=_HEADERS) as client:
        for batch_start in range(2, total_pages + 1, 50):
            pages = range(batch_start, min(batch_start + 50, total_pages + 1))
            results = await asyncio.gather(*[
                _fetch_page(client, bgn_de, end_de, p) for p in pages
            ])
            for chunk, _, _ in results:
                items.extend(chunk)
            print(".", end="", flush=True)

    print(f" ({len(items)}건)")
    return items


async def fetch_a001_corps() -> dict[str, dict]:
    """A001 최근 2개 공시연도 전체 수집 → {corp_code: {...}}

    DART list.json 제약: corp_code 없이 조회 시 기간 3개월 이내
    → 분기별(Q1~Q4)로 나눠서 수집, 미래 분기 자동 스킵
    """
    filing_years = [TODAY.year - 1, TODAY.year]
    all_corps: dict[str, dict] = {}
    today_str = TODAY.strftime("%Y%m%d")

    for year in filing_years:
        quarters = [
            (f"{year}0101", f"{year}0331"),
            (f"{year}0401", f"{year}0630"),
            (f"{year}0701", f"{year}0930"),
            (f"{year}1001", f"{year}1231"),
        ]
        # 시작일이 오늘 이후인 분기는 스킵
        quarters = [(b, e) for b, e in quarters if b <= today_str]

        if not quarters:
            continue

        print(f"\n  [{year}년]")
        year_before = len(all_corps)

        for bgn_de, end_de in quarters:
            items = await _fetch_quarter(bgn_de, end_de)
            for item in items:
                code = (item.get("corp_code") or "").strip()
                if code and code not in all_corps:
                    all_corps[code] = {
                        "corp_code":  code,
                        "corp_name":  (item.get("corp_name")  or "").strip(),
                        "stock_code": (item.get("stock_code") or "").strip(),
                        "corp_cls":   (item.get("corp_cls")   or "").strip(),
                    }

        year_new = len(all_corps) - year_before
        print(f"    {year}년 신규: {year_new:,}개")

    print(f"\n  A001 기업: {len(all_corps):,}개 (2개년 합산, 중복 제거)")
    return all_corps


# ── 유니버스 합산 ─────────────────────────────────────────────────────────────

def build_universe(
    a001_corps: dict[str, dict],
    retain_viewer: list[dict],
) -> list[dict]:
    """
    대상 = A001 ∪ Retain Viewer

    - corp_code 기준 합집합
    - 이름은 Retain Viewer의 official_name 우선 (매칭 품질이 더 높음)
    - Retain Viewer 중 corp_code 없는 항목(not_found)은 마지막에 별도 포함:
        수집 스킵 대상이지만 포털 검색에는 표시
    """
    universe: dict[str, dict] = {}

    # ── 1. A001 기업 추가
    for code, co in a001_corps.items():
        universe[code] = {
            "corp_code":        code,
            "corp_name":        co["corp_name"],
            "official_name":    co["corp_name"],
            "candidate_name":   co["corp_name"],
            "stock_code":       co["stock_code"],
            "corp_cls":         co["corp_cls"],
            "is_a001":          True,
            "is_retain_viewer": False,
        }

    # ── 2. Retain Viewer 병합
    no_code_items: list[dict] = []

    for rv in retain_viewer:
        code = (rv.get("corp_code") or "").strip()

        if not code:
            # not_found: corp_code 없음 → 수집 스킵, 검색 표시 전용
            no_code_items.append({
                "corp_code":        "",
                "corp_name":        rv.get("official_name") or rv.get("candidate_name", ""),
                "official_name":    rv.get("official_name", ""),
                "candidate_name":   rv.get("candidate_name", ""),
                "stock_code":       "",
                "corp_cls":         "",
                "is_a001":          False,
                "is_retain_viewer": True,
            })
            continue

        if code in universe:
            # A001 + Retain Viewer 양쪽 - official_name은 Retain Viewer 우선
            universe[code]["is_retain_viewer"] = True
            if rv.get("official_name"):
                universe[code]["official_name"]  = rv["official_name"]
            if rv.get("candidate_name"):
                universe[code]["candidate_name"] = rv["candidate_name"]
        else:
            # Retain Viewer 전용 (A001 미제출 기업)
            universe[code] = {
                "corp_code":        code,
                "corp_name":        rv.get("official_name") or rv.get("candidate_name", ""),
                "official_name":    rv.get("official_name", ""),
                "candidate_name":   rv.get("candidate_name", ""),
                "stock_code":       rv.get("stock_code", ""),
                "corp_cls":         rv.get("corp_cls", ""),
                "is_a001":          False,
                "is_retain_viewer": True,
            }

    # ── 3. 통계 출력
    corps = list(universe.values())
    a001_only   = sum(1 for c in corps if c["is_a001"] and not c["is_retain_viewer"])
    both        = sum(1 for c in corps if c["is_a001"] and     c["is_retain_viewer"])
    rv_only     = sum(1 for c in corps if not c["is_a001"] and c["is_retain_viewer"])

    print(f"\n  ── 기업 유니버스 구성 ──────────────────────────")
    print(f"  A001 전용 (Retain Viewer 外):        {a001_only:>5,}개")
    print(f"  A001 + Retain Viewer 양쪽:           {both:>5,}개")
    print(f"  Retain Viewer 전용 (A001 미제출):    {rv_only:>5,}개")
    print(f"  ─────────────────────────────────────────────")
    print(f"  수집 대상 소계 (corp_code 있음):     {len(corps):>5,}개")
    print(f"  Retain Viewer 中 DART 미등록 (별도): {len(no_code_items):>5,}개")
    print(f"  최종 목록 합계:                      {len(corps)+len(no_code_items):>5,}개")

    # corp_code 있는 것 정렬 후 not_found 뒤에 추가
    corps_sorted = sorted(corps, key=lambda x: x["corp_code"])
    corps_sorted.extend(no_code_items)
    return corps_sorted


# ── F001 존재 여부 스캔 ──────────────────────────────────────────────────────

async def _check_one_f001(client: httpx.AsyncClient, corp_code: str) -> bool:
    """단일 기업 F001 공시 존재 여부 확인 (최근 3년)"""
    for attempt in range(1, 5):
        try:
            async with F001_SEM:
                r = await client.get(
                    "https://opendart.fss.or.kr/api/list.json",
                    params={
                        "crtfc_key":          DART_KEY,
                        "corp_code":          corp_code,
                        "pblntf_detail_ty":   "F001",
                        "bgn_de":             f"{TODAY.year - 3}0101",
                        "end_de":             TODAY.strftime("%Y%m%d"),
                        "page_no":            1,
                        "page_count":         1,
                    },
                    timeout=20,
                )
            data = r.json()
            status = data.get("status", "")
            if status == "000":
                return len(data.get("list", [])) > 0
            # 013 = 조회 결과 없음 → False (정상 응답)
            return False
        except Exception:
            if attempt < 4:
                await asyncio.sleep(2 ** attempt)  # 지수 백오프: 2, 4, 8초
    return False


async def scan_f001_existence(corps: list[dict]) -> dict[str, bool]:
    """비A001 기업 전체에 대해 F001 공시 존재 여부 일괄 스캔"""
    total = len(corps)
    results: dict[str, bool] = {}

    print(f"\n  F001 공시 존재 여부 스캔 시작 ({total:,}개) ...")
    print(f"  (동시 요청 3개, 배치 30개, 배치 간 1.5초 대기 - ConnectionReset 방지)")

    batch_size = 30  # 50 → 30 (과부하 방지)
    async with httpx.AsyncClient(verify=False, headers=_HEADERS, timeout=20) as client:
        for i in range(0, total, batch_size):
            batch = corps[i : i + batch_size]
            batch_results = await asyncio.gather(*[
                _check_one_f001(client, c["corp_code"]) for c in batch
            ])
            for c, has_f001 in zip(batch, batch_results):
                results[c["corp_code"]] = has_f001

            done   = min(i + batch_size, total)
            f001_n = sum(1 for v in results.values() if v)
            print(f"  진행: {done:,}/{total:,}  F001 발견: {f001_n:,}개", end="\r")
            await asyncio.sleep(1.5)  # 0.3 → 1.5초 (DART 서버 ConnectionReset 방지)

    print()  # 줄 바꿈
    return results


def _dart_level(corp: dict) -> str:
    """dart_data_level 결정"""
    if not corp.get("corp_code"):
        return "none"
    if corp.get("is_a001"):
        return "a001"
    if corp.get("has_f001"):
        return "f001"
    return "basic"


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    force = "--force" in sys.argv

    _setup_windows_proxy()  # 회사망 프록시 자동 감지

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "company_master_full.json"

    age = _cache_age_days(out_path)
    if not force and age is not None and age < CACHE_DAYS:
        print(f"=== 기업 목록 캐시 재사용 ({age:.1f}일 전 생성) ===")
        print(f"  경로: {out_path}")
        print(f"  강제 재생성: python dart_master.py --force")
        return

    orig_data = json.loads(ORIG_MASTER.read_text(encoding="utf-8"))
    retain_viewer = (
        orig_data.get("companies", orig_data) if isinstance(orig_data, dict)
        else orig_data
    )

    print("=== DART A001 ∪ Retain Viewer 기업 유니버스 생성 ===")
    print(f"  Retain Viewer 모집단: {len(retain_viewer):,}개")
    print(f"  DART 수집 방식: list.json A001 페이지네이션")
    print(f"  대상 공시연도: {TODAY.year - 1}년 ~ {TODAY.year}년")

    if not _diagnose():
        print("[오류] DART API 연결 실패.")
        print(f"  API 키: {DART_KEY[:8]}...{DART_KEY[-4:]}")
        print("  브라우저에서 https://opendart.fss.or.kr 접속 가능 여부 확인")
        sys.exit(1)

    a001_corps = asyncio.run(fetch_a001_corps())

    if not a001_corps:
        print("\n[오류] A001 기업 목록 수집 실패. API 키 또는 네트워크 확인.")
        sys.exit(1)

    companies = build_universe(a001_corps, retain_viewer)

    # ── F001 존재 여부 스캔 (비A001 기업만)
    non_a001 = [c for c in companies if not c.get("is_a001") and c.get("corp_code")]
    f001_map: dict[str, bool] = {}
    if non_a001:
        f001_map = asyncio.run(scan_f001_existence(non_a001))

    # ── has_f001 / dart_data_level 필드 부여
    for c in companies:
        code = c.get("corp_code", "")
        if c.get("is_a001"):
            c["has_f001"] = True   # A001 기업은 F001도 제출하는 경우가 많으나 A001 우선
        elif code:
            c["has_f001"] = f001_map.get(code, False)
        else:
            c["has_f001"] = False
        c["dart_data_level"] = _dart_level(c)

    # ── 최종 통계
    lv = {k: sum(1 for c in companies if c.get("dart_data_level") == k)
          for k in ("a001", "f001", "basic", "none")}
    print(f"\n  ── 데이터 수준별 최종 집계 ─────────────────────")
    print(f"  a001  (풀데이터):    {lv['a001']:>5,}개")
    print(f"  f001  (부분데이터):  {lv['f001']:>5,}개")
    print(f"  basic (기본정보만):  {lv['basic']:>5,}개")
    print(f"  none  (DART 없음):   {lv['none']:>5,}개")
    print(f"  ───────────────────────────────────────────────")
    print(f"  합계:                {len(companies):>5,}개")

    collect_count = lv["a001"] + lv["f001"] + lv["basic"]

    out = {
        "generated_at":  TODAY.isoformat(),
        "source":        f"DART A001 ({TODAY.year-1}~{TODAY.year}) ∪ Retain Viewer + F001 스캔",
        "total":         len(companies),
        "collect_count": collect_count,
        "level_counts":  lv,
        "companies":     companies,
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장 완료: {out_path}")


if __name__ == "__main__":
    main()
