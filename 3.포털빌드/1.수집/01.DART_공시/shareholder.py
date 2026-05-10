"""
04_fetch_shareholder.py -- F-06 주주현황 수집
DART majorstock.json API (5% 이상 대량보유자)
결과: cache/{corp_code}_shareholder_{YYYYMMDD}.json

실행: python fetch/04_fetch_shareholder.py [최대개수]
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
CACHE_DIR  = CACHE_ROOT / "04.대량보유"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(2)  # 동시 2개 제한


async def fetch_one(client: httpx.AsyncClient, corp_code: str) -> None:
    out = CACHE_DIR / f"{corp_code}_shareholder_{TODAY}.json"
    if out.exists():
        return

    data = None
    net_errors = 0
    while net_errors < 3:
        try:
            async with SEM:
                r = await client.get(
                    "https://opendart.fss.or.kr/api/majorstock.json",
                    params={"crtfc_key": KEY_MGR.current, "corp_code": corp_code},
                    timeout=20,
                )
            data = r.json()
            if data.get("status") in ROTATE_STATUSES:
                if KEY_MGR.rotate(data["status"]):
                    continue
                return
            break
        except Exception as e:
            net_errors += 1
            if net_errors < 3:
                await asyncio.sleep(5 * net_errors)
            else:
                print(f"  오류 [{corp_code}] {type(e).__name__}")
                return

    if data and data.get("status") == "000" and data.get("list"):
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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

    already = sum(
        1 for cc in corp_codes
        if (CACHE_DIR / f"{cc}_shareholder_{TODAY}.json").exists()
    )
    print(f"주주현황 수집 대상: {len(corp_codes)}개 (당일 캐시: {already}개)")

    batch = 30
    async with httpx.AsyncClient(verify=False) as client:
        for i in range(0, len(corp_codes), batch):
            chunk = corp_codes[i: i + batch]
            await asyncio.gather(*[fetch_one(client, cc) for cc in chunk])
            done = sum(
                1 for cc in corp_codes[: i + batch]
                if (CACHE_DIR / f"{cc}_shareholder_{TODAY}.json").exists()
            )
            print(
                f"  진행: {min(i+batch, len(corp_codes))}/{len(corp_codes)}"
                f"  (저장: {done}개 -- 데이터 없음은 제외)"
            )
            await asyncio.sleep(2)

    total = sum(
        1 for cc in corp_codes
        if (CACHE_DIR / f"{cc}_shareholder_{TODAY}.json").exists()
    )
    print(f"\n완료: {total}개 저장 (데이터 없는 회사 제외)")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
