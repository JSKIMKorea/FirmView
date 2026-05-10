"""
08_fetch_reports.py -- 정기 공시 보고서 (사업보고서/감사보고서) 수집
01_fetch_company.py 캐시 기반으로 DART list.json 호출.
- 사업보고서(A001) + 감사보고서(F001) 모두 조회 후 결산기 기준 중복 제거
- 분기/반기 보고서는 제외 (1년 단위만)
- 가장 최근 3개년치만 저장 (rcept_no 포함, 클릭 시 DART 원문으로 이동)

결과: cache/{corp_code}_reports_{YYYYMMDD}.json
실행: python fetch/08_fetch_reports.py [최대개수]
"""
import asyncio
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import httpx
from dart_key_manager import KEY_MGR, ROTATE_STATUSES

ROOT = Path(__file__).parent.parent.parent.parent
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "08.정기공시보고서"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(3)  # 동시 3개 제한

# 결산기 패턴: report_nm 끝의 (YYYY.MM)
_STLM_RE = re.compile(r"\((\d{4})\.(\d{2})\)\s*$")


async def _fetch_list(client: httpx.AsyncClient, corp_code: str, detail_ty: str) -> list:
    net_errors = 0
    while net_errors < 3:
        try:
            r = await client.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": KEY_MGR.current,
                    "corp_code": corp_code,
                    "bgn_de": "20220101",
                    "end_de": TODAY,
                    "pblntf_detail_ty": detail_ty,
                    "page_count": 30,
                    "sort": "date",
                    "sort_mth": "desc",
                },
                timeout=20,
            )
            d = r.json()
            if d.get("status") in ROTATE_STATUSES:
                if KEY_MGR.rotate(d["status"]):
                    continue
                return []
            if d.get("status") == "000" and d.get("list"):
                return d["list"]
            return []
        except Exception:
            net_errors += 1
            if net_errors < 3:
                await asyncio.sleep(5 * net_errors)
    return []


def _extract_stlm(report_nm: str) -> str | None:
    """report_nm에서 결산기 추출 ((2024.12) → '2024.12')"""
    m = _STLM_RE.search(report_nm or "")
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return None


def _categorize(report_nm: str) -> str | None:
    """1년 단위 정기 공시인 경우 '사업보고서' 또는 '감사보고서' 반환, 그 외 None"""
    if not report_nm:
        return None
    if "분기" in report_nm or "반기" in report_nm:
        return None
    if "사업보고서" in report_nm:
        return "사업보고서"
    if "감사보고서" in report_nm:
        return "감사보고서"
    return None


async def fetch_one(client: httpx.AsyncClient, corp_code: str) -> None:
    out = CACHE_DIR / f"{corp_code}_reports_{TODAY}.json"
    if out.exists():
        return

    async with SEM:
        # 사업보고서 + 감사보고서 동시 조회
        a_items, f_items = await asyncio.gather(
            _fetch_list(client, corp_code, "A001"),
            _fetch_list(client, corp_code, "F001"),
        )
        items = a_items + f_items

        # 결산기별 그룹핑 (가장 최근 rcept_dt만 유지, 사업보고서 우선)
        by_stlm: dict[str, dict] = {}
        for it in items:
            nm = (it.get("report_nm") or "").strip()
            kind = _categorize(nm)
            if not kind:
                continue
            stlm = _extract_stlm(nm)
            if not stlm:
                continue
            rcept_dt = it.get("rcept_dt", "")
            rcept_no = it.get("rcept_no", "")
            if not rcept_dt or not rcept_no:
                continue

            entry = {
                "rcept_no": rcept_no,
                "rcept_dt": rcept_dt,
                "report_nm": nm,
                "kind": kind,
                "stlm": stlm,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
            }
            cur = by_stlm.get(stlm)
            if cur is None:
                by_stlm[stlm] = entry
            else:
                # 같은 결산기 내: 사업보고서 > 감사보고서, 그 다음 rcept_dt 최신
                cur_pri = 0 if cur["kind"] == "사업보고서" else 1
                new_pri = 0 if kind == "사업보고서" else 1
                if new_pri < cur_pri or (new_pri == cur_pri and rcept_dt > cur["rcept_dt"]):
                    by_stlm[stlm] = entry

        # 결산기 기준 최신 3개년만 (사업보고서/감사보고서 혼재 가능)
        sorted_entries = sorted(by_stlm.values(), key=lambda e: e["stlm"], reverse=True)[:3]

        result = {
            "corp_code": corp_code,
            "items": sorted_entries,
        }
        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )


async def main(limit: int = 0) -> None:
    _pm = os.environ.get("PORTAL_MASTER")
    if _pm:
        _raw = json.loads(Path(_pm).read_text(encoding="utf-8"))
        _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
        corp_codes = [c["corp_code"] for c in _comps if c.get("corp_code")]
    else:
        corp_codes = sorted(
            {p.stem.split("_company_")[0] for p in COMPANY_CACHE_DIR.glob("*_company_*.json")}
        )
    if limit:
        corp_codes = corp_codes[:limit]

    print(f"공시 보고서 수집 대상: {len(corp_codes)}개")
    async with httpx.AsyncClient(verify=False) as client:
        for i in range(0, len(corp_codes), 50):
            chunk = corp_codes[i: i + 50]
            await asyncio.gather(*[fetch_one(client, cc) for cc in chunk])
            print(f"  진행: {min(i+50, len(corp_codes))}/{len(corp_codes)}")
            await asyncio.sleep(1)

    print("완료")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
