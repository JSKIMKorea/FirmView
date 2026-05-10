"""
02_fetch_financial.py -- F-02 재무정보 + 감사의견 수집

누적 캐시 방식:
  - A001: 연도별 영구 캐시 permanent/{corp_code}_{year}.json
           한 번 저장하면 이후 실행에서 API 미호출 (보고서 내용 불변)
           일별 출력 파일은 영구 캐시 머지로 빠르게 생성
  - F001: 감사보고서 링크 stub (Playwright 스크립트에서 별도 추출)
  - Basic/None: 스킵

결과: cache/02.재무제표_감사의견/{corp_code}_financial_{YYYYMMDD}.json
      cache/02.재무제표_감사의견/permanent/{corp_code}_{year}.json

실행: python fetch/02_fetch_financial.py [최대개수]
"""
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

import httpx
from dart_key_manager import KEY_MGR, ROTATE_STATUSES

ROOT = Path(__file__).parent.parent.parent.parent
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "02.재무제표_감사의견"
PERM_DIR   = CACHE_DIR / "permanent"           # 연도별 영구 캐시
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"
REPORTS_CACHE_DIR = CACHE_ROOT / "08.정기공시보고서"
FULL_MASTER = CACHE_ROOT / "00.회사목록" / "company_master_full.json"
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(1)  # 동시 1개 — fnlttSinglAcntAll 일일한도 절약
REPRT_CODE = "11011"  # 사업보고서

_TODAY_DATE = date.today()
YEARS = [_TODAY_DATE.year - 1, _TODAY_DATE.year - 2, _TODAY_DATE.year - 3]

_OPINION_NORMALIZE = {
    "적정의견": "적정", "한정의견": "한정", "부적정의견": "부적정",
    "의견거절": "의견거절", "적정": "적정", "한정": "한정", "부적정": "부적정",
}


def _normalize_opinion(raw: str) -> str:
    if not raw:
        return ""
    first = raw.split("\n")[0].strip()
    normalized = _OPINION_NORMALIZE.get(first, first)
    if normalized.endswith("의견") and normalized != "의견거절":
        normalized = normalized[:-2]
    return normalized


def _extract_audit(audit_item: dict) -> dict:
    raw_opinion = (
        audit_item.get("adt_opinion", "")
        or audit_item.get("adtor_opnion", "")
    )
    auditor = (
        audit_item.get("adtor", "")
        or audit_item.get("auditor_nm", "")
        or audit_item.get("adtor_nm", "")
    )
    auditor = auditor.split("\n")[0].strip()
    return {
        "opinion": _normalize_opinion(raw_opinion),
        "auditor": auditor,
        "core_matter": audit_item.get("core_adt_matter", ""),
        "rcept_no": audit_item.get("rcept_no", ""),
        "stlm_dt": audit_item.get("stlm_dt", ""),
    }


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    net_errors = 0
    while net_errors < 3:
        try:
            p = {**params, "crtfc_key": KEY_MGR.current}
            r = await client.get(url, params=p, timeout=20)
            data = r.json()
            if data.get("status") in ROTATE_STATUSES:
                if KEY_MGR.rotate(data["status"]):
                    continue  # 새 키로 즉시 재시도
                return {}
            return data
        except Exception:
            net_errors += 1
            if net_errors < 3:
                await asyncio.sleep(5 * net_errors)
    return {}


# ── 영구 캐시 헬퍼 ─────────────────────────────────────────
def _perm_path(corp_code: str, year: str) -> Path:
    return PERM_DIR / f"{corp_code}_{year}.json"


def _load_perm(corp_code: str, year: str) -> dict | None:
    p = _perm_path(corp_code, year)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _save_perm(corp_code: str, year: str, data: dict) -> None:
    PERM_DIR.mkdir(parents=True, exist_ok=True)
    _perm_path(corp_code, year).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── A001 재무 수집 ─────────────────────────────────────────
async def fetch_one(client: httpx.AsyncClient, corp_code: str) -> tuple[bool, int, int]:
    """(저장여부, 캐시hit수, API호출수) 반환"""
    out = CACHE_DIR / f"{corp_code}_financial_{TODAY}.json"
    if out.exists():
        return False, 0, 0

    async with SEM:
        result = {"corp_code": corp_code, "years": {}}
        cache_hits = 0
        api_calls = 0

        for year in YEARS:
            year_str = str(year)

            # 영구 캐시 우선 확인
            cached = _load_perm(corp_code, year_str)
            if cached:
                result["years"][year_str] = cached
                cache_hits += 1
                continue

            # 영구 캐시 없음 - DART API 호출
            year_data: dict = {"fs": {}, "audit": {}}

            for fs_div in ("CFS", "OFS"):
                data = await _get(
                    client,
                    "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                    {
                        "corp_code": corp_code,
                        "bsns_year": year_str,
                        "reprt_code": REPRT_CODE,
                        "fs_div": fs_div,
                    },
                )
                if data.get("status") == "000" and data.get("list"):
                    year_data["fs"][fs_div] = data["list"]

            audit_data = await _get(
                client,
                "https://opendart.fss.or.kr/api/accnutAdtorNmNdAdtOpinion.json",
                {
                    "corp_code": corp_code,
                    "bsns_year": year_str,
                    "reprt_code": REPRT_CODE,
                },
            )
            if audit_data.get("status") == "000" and audit_data.get("list"):
                items = audit_data["list"]
                chosen = None
                for item in items:
                    by = item.get("bsns_year", "")
                    if "결산" in by or ("기" in by and "분기" not in by and "반기" not in by):
                        chosen = item
                        break
                if not chosen:
                    chosen = items[0]
                year_data["audit"] = _extract_audit(chosen)

            api_calls += 1
            await asyncio.sleep(0.5)  # 연도별 0.5초 대기 — 분당 약 120호출 제한

            if year_data["fs"] or year_data["audit"].get("opinion"):
                result["years"][year_str] = year_data
                _save_perm(corp_code, year_str, year_data)  # 영구 캐시 저장

        if result["years"]:
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return True, cache_hits, api_calls

        return False, cache_hits, api_calls


# ── F001 stub ─────────────────────────────────────────────
async def fetch_one_f001(corp_code: str) -> None:
    """f001 기업: 구조화 재무 API 없음 - 감사보고서 링크만 stub으로 저장"""
    out = CACHE_DIR / f"{corp_code}_financial_{TODAY}.json"
    if out.exists():
        return

    reports_files = sorted(REPORTS_CACHE_DIR.glob(f"{corp_code}_reports_*.json"), reverse=True)
    f001_reports = []
    if reports_files:
        data = json.loads(reports_files[0].read_text(encoding="utf-8"))
        for it in data.get("items", []):
            if it.get("kind") in ("감사보고서", "사업보고서"):
                f001_reports.append({
                    "rcept_no": it.get("rcept_no", ""),
                    "stlm":     it.get("stlm", ""),
                    "rcept_dt": it.get("rcept_dt", ""),
                    "kind":     it.get("kind", ""),
                    "url":      it.get("url", ""),
                })

    result = {
        "corp_code":        corp_code,
        "dart_data_level":  "f001",
        "years":            {},
        "f001_reports":     f001_reports,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 메인 ──────────────────────────────────────────────────
async def main(limit: int = 0) -> None:
    _pm = os.environ.get("PORTAL_MASTER")
    if _pm:
        _raw = json.loads(Path(_pm).read_text(encoding="utf-8"))
        _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
    else:
        if FULL_MASTER.exists():
            _raw = json.loads(FULL_MASTER.read_text(encoding="utf-8"))
            _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
        else:
            _comps = []

    level_map: dict[str, str] = {
        c["corp_code"]: c.get("dart_data_level", "a001")
        for c in _comps if c.get("corp_code")
    }

    if _pm or level_map:
        corp_codes = [c["corp_code"] for c in _comps if c.get("corp_code")]
    else:
        corp_codes = sorted(
            {p.stem.split("_company_")[0] for p in COMPANY_CACHE_DIR.glob("*_company_*.json")}
        )

    if limit:
        corp_codes = corp_codes[:limit]

    a001_codes = [cc for cc in corp_codes if level_map.get(cc, "a001") == "a001"]
    f001_codes = [cc for cc in corp_codes if level_map.get(cc) == "f001"]

    already     = sum(1 for cc in corp_codes if (CACHE_DIR / f"{cc}_financial_{TODAY}.json").exists())
    perm_count  = sum(1 for cc in a001_codes
                      if any(_perm_path(cc, str(y)).exists() for y in YEARS))

    print(f"재무정보 수집 대상: {len(corp_codes)}개 "
          f"(a001:{len(a001_codes)} f001:{len(f001_codes)}) "
          f"/ 당일 캐시: {already}개 / 영구 캐시 보유: {perm_count}개사")

    # f001 stub (빠름)
    for cc in f001_codes:
        await fetch_one_f001(cc)

    # a001 재무 API (영구 캐시 미보유 연도만 호출)
    total_hits = total_calls = 0
    batch = 30
    async with httpx.AsyncClient(verify=False) as client:
        for i in range(0, len(a001_codes), batch):
            chunk = a001_codes[i: i + batch]
            results = await asyncio.gather(*[fetch_one(client, cc) for cc in chunk])
            for _, hits, calls in results:
                total_hits  += hits
                total_calls += calls
            done = sum(1 for cc in a001_codes[: i + batch]
                       if (CACHE_DIR / f"{cc}_financial_{TODAY}.json").exists())
            print(f"  진행(a001): {min(i+batch, len(a001_codes))}/{len(a001_codes)}"
                  f"  저장:{done}개  캐시hit:{total_hits}  API호출:{total_calls}")
            await asyncio.sleep(1)

    print("완료")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
