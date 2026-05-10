"""
09_fetch_shareholder_full.py -- 주석 1번 수준의 전체 주주 구성 수집

누적 캐시 방식:
  permanent/{corp_code}_{year}.json -- 연도별 영구 보관
  동일 연도 데이터 재실행 시 DART API 미호출 (보고서 내용 불변)

API:
  hyslrSttus.json  -- 최대주주 등 주식소유 현황 (사업보고서 주석1 기준)
  mrhlSttus.json   -- 소액주주현황 (상장사 한정)

결과: cache/09.주주현황_보고서주석/{corp_code}_shareholder_full_{YYYYMMDD}.json
      cache/09.주주현황_보고서주석/permanent/{corp_code}_{year}.json

실행: python fetch/09_fetch_shareholder_full.py [최대개수]
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
CACHE_DIR  = CACHE_ROOT / "09.주주현황_보고서주석"
PERM_DIR   = CACHE_DIR / "permanent"           # 연도별 영구 캐시
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"
FULL_MASTER = CACHE_ROOT / "00.회사목록" / "company_master_full.json"
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(3)
REPRT_CODE = "11011"  # 사업보고서

_TODAY = date.today()
YEARS = [_TODAY.year - 1, _TODAY.year - 2, _TODAY.year - 3]


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


async def _fetch(client: httpx.AsyncClient, api: str, params: dict) -> dict:
    net_errors = 0
    while net_errors < 3:
        try:
            p = {**params, "crtfc_key": KEY_MGR.current}
            r = await client.get(
                f"https://opendart.fss.or.kr/api/{api}.json",
                params=p, timeout=20,
            )
            data = r.json()
            if data.get("status") in ROTATE_STATUSES:
                if KEY_MGR.rotate(data["status"]):
                    continue
                return {}
            return data
        except Exception:
            net_errors += 1
            if net_errors < 3:
                await asyncio.sleep(5 * net_errors)
    return {}


async def fetch_one(client: httpx.AsyncClient, corp_code: str) -> tuple[bool, int, int]:
    """(저장여부, 캐시hit수, API호출수) 반환"""
    out = CACHE_DIR / f"{corp_code}_shareholder_full_{TODAY}.json"
    if out.exists():
        return False, 0, 0

    async with SEM:
        majors: list = []
        small_summary = None
        used_year = None
        rcept_no = ""
        cache_hits = 0
        api_calls = 0

        for yr in YEARS:
            year_str = str(yr)

            # 영구 캐시 확인
            cached = _load_perm(corp_code, year_str)
            if cached and cached.get("majors"):
                majors = cached["majors"]
                small_summary = cached.get("small_summary")
                used_year = year_str
                rcept_no = cached.get("rcept_no", "")
                cache_hits += 1
                break  # 유효한 캐시 발견 - 종료

            # DART API 호출
            params = {
                "corp_code": corp_code,
                "bsns_year": year_str,
                "reprt_code": REPRT_CODE,
            }
            d = await _fetch(client, "hyslrSttus", params)
            api_calls += 1

            if d.get("status") == "000" and d.get("list"):
                items = []
                for it in d["list"]:
                    nm = (it.get("nm") or "").strip()
                    if nm in ("", "계", "-"):
                        continue
                    items.append({
                        "nm":       nm,
                        "relate":   (it.get("relate") or "").strip(),
                        "stock_knd": (it.get("stock_knd") or "").strip(),
                        "stkqy":    (it.get("bsis_posesn_stock_co") or "").replace(",", "").strip(),
                        "stkrt":    (it.get("bsis_posesn_stock_qota_rt") or "").strip(),
                        "stkqy_end": (it.get("trmend_posesn_stock_co") or "").replace(",", "").strip(),
                        "stkrt_end": (it.get("trmend_posesn_stock_qota_rt") or "").strip(),
                    })
                if items:
                    majors = items
                    used_year = year_str
                    rcept_no = d["list"][0].get("rcept_no", "")
                    break  # 최신 데이터 발견 - 종료

        # 소액주주 (used_year가 있을 때만 조회)
        if used_year and not cache_hits:  # 캐시히트 시에는 이미 포함됨
            d2 = await _fetch(client, "mrhlSttus", {
                "corp_code": corp_code,
                "bsns_year": used_year,
                "reprt_code": REPRT_CODE,
            })
            api_calls += 1
            if d2.get("status") == "000" and d2.get("list"):
                for it in d2["list"]:
                    if it.get("se", "").strip() == "소액주주":
                        small_summary = {
                            "shrholdr_co":  (it.get("shrholdr_co") or "").replace(",", ""),
                            "hold_stock_co": (it.get("hold_stock_co") or "").replace(",", ""),
                            "stock_tot_co": (it.get("stock_tot_co") or "").replace(",", ""),
                            "shrholdr_rate": (it.get("shrholdr_rate") or "").replace("%", "").strip(),
                            "stock_rate": "",
                        }
                        try:
                            hold = int(small_summary["hold_stock_co"] or 0)
                            tot  = int(small_summary["stock_tot_co"] or 0)
                            if tot > 0:
                                small_summary["stock_rate"] = round(hold / tot * 100, 2)
                        except (ValueError, TypeError):
                            pass
                        break

        result = {
            "corp_code":    corp_code,
            "year":         used_year,
            "rcept_no":     rcept_no,
            "majors":       majors,
            "small_summary": small_summary,
        }

        if majors or small_summary:
            # 영구 캐시 저장 (캐시미스로 새로 가져온 경우만)
            if used_year and not cache_hits:
                _save_perm(corp_code, used_year, result)
            out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True, cache_hits, api_calls

        return False, cache_hits, api_calls


async def main(limit: int = 0) -> None:
    _pm = os.environ.get("PORTAL_MASTER")
    if _pm:
        _raw = json.loads(Path(_pm).read_text(encoding="utf-8"))
        _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
        # a001만 - hyslrSttus는 사업보고서 기업만 지원
        corp_codes = [c["corp_code"] for c in _comps
                      if c.get("corp_code") and c.get("dart_data_level", "a001") == "a001"]
    else:
        if FULL_MASTER.exists():
            _raw = json.loads(FULL_MASTER.read_text(encoding="utf-8"))
            _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
            # a001만 - hyslrSttus는 사업보고서 기업만 지원
            corp_codes = [c["corp_code"] for c in _comps
                          if c.get("corp_code") and c.get("dart_data_level", "a001") == "a001"]
        else:
            corp_codes = sorted(
                {p.stem.split("_company_")[0] for p in COMPANY_CACHE_DIR.glob("*_company_*.json")}
            )

    if limit:
        corp_codes = corp_codes[:limit]

    already    = sum(1 for cc in corp_codes if (CACHE_DIR / f"{cc}_shareholder_full_{TODAY}.json").exists())
    perm_count = sum(1 for cc in corp_codes if any(_perm_path(cc, str(y)).exists() for y in YEARS))

    print(f"주주현황(주석 기준) 수집 대상: {len(corp_codes)}개 "
          f"/ 당일 캐시: {already}개 / 영구 캐시 보유: {perm_count}개사")

    total_hits = total_calls = 0
    async with httpx.AsyncClient(verify=False) as client:
        for i in range(0, len(corp_codes), 50):
            chunk = corp_codes[i: i + 50]
            results = await asyncio.gather(*[fetch_one(client, cc) for cc in chunk])
            for _, hits, calls in results:
                total_hits  += hits
                total_calls += calls
            done = sum(1 for cc in corp_codes[: i + 50]
                       if (CACHE_DIR / f"{cc}_shareholder_full_{TODAY}.json").exists())
            print(f"  진행: {min(i+50, len(corp_codes))}/{len(corp_codes)}"
                  f"  저장:{done}개  캐시hit:{total_hits}  API호출:{total_calls}")
            await asyncio.sleep(1)

    print("완료")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
