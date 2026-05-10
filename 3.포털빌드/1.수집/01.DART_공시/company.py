"""
01_fetch_company.py -- F-01 기업기본정보 수집
company_master.json 전체 회사(corp_code 있는 것)에 대해 DART company.json 호출
결과: cache/{corp_code}_company_{YYYYMMDD}.json

실행: python fetch/01_fetch_company.py [최대개수]
  예: python fetch/01_fetch_company.py 100   # 테스트용
      python fetch/01_fetch_company.py        # 전체 실행
"""
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

import httpx
from dart_key_manager import KEY_MGR, ROTATE_STATUSES

ROOT = Path(__file__).parent.parent.parent.parent          # 종합기업정보/
# PORTAL_MASTER 환경변수가 있으면 그 파일 사용 (--full-dart 모드)
MASTER = Path(os.environ["PORTAL_MASTER"]) if "PORTAL_MASTER" in os.environ \
    else ROOT / "1.회사명정리/output/company_master.json"
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "01.기업개황"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(2)  # 동시 호출 2개 제한 (DART 서버 부하 조절)


async def fetch_one(client: httpx.AsyncClient, company: dict) -> bool:
    corp_code = company["corp_code"]
    out = CACHE_DIR / f"{corp_code}_company_{TODAY}.json"
    if out.exists():
        return True  # 당일 캐시 있음

    data = None
    net_errors = 0
    while net_errors < 3:
        try:
            async with SEM:
                r = await client.get(
                    "https://opendart.fss.or.kr/api/company.json",
                    params={"crtfc_key": KEY_MGR.current, "corp_code": corp_code},
                    timeout=20,
                )
            data = r.json()
            if data.get("status") in ROTATE_STATUSES:
                if KEY_MGR.rotate(data["status"]):
                    continue  # 새 키로 즉시 재시도
                return False  # 모든 키 소진
            break
        except Exception as e:
            net_errors += 1
            if net_errors < 3:
                await asyncio.sleep(5 * net_errors)
            else:
                print(f"  오류 [{corp_code}] {type(e).__name__}")
                return False

    if data is None:
        return False

    if data.get("status") == "000":
        data["_master"] = {
            "candidate_name":   company.get("candidate_name", ""),
            "official_name":    company.get("official_name", ""),
            "match_type":       company.get("match_type", ""),
            "confidence":       company.get("confidence", 0),
            "is_a001":          company.get("is_a001", False),
            "is_retain_viewer": company.get("is_retain_viewer", False),
        }
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for old in CACHE_DIR.glob(f"{corp_code}_company_*.json"):
            if old != out:
                old.unlink()
        return True
    elif data.get("status") == "013":
        return False  # 조회 결과 없음 -- 정상적 스킵
    else:
        print(f"  DART 오류 [{corp_code}] {data.get('status')} {data.get('message', '')}")
        return False


async def main(limit: int = 0) -> None:
    raw = json.loads(MASTER.read_text(encoding="utf-8"))
    companies = raw.get("companies", raw) if isinstance(raw, dict) else raw
    targets = [c for c in companies if c.get("corp_code")]
    if limit:
        targets = targets[:limit]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    already = sum(
        1 for c in targets
        if (CACHE_DIR / f"{c['corp_code']}_company_{TODAY}.json").exists()
    )
    print(f"대상: {len(targets)}개 (당일 캐시: {already}개, 신규 수집: {len(targets)-already}개)")

    batch = 30  # 배치 크기 (서버 부하 분산)
    async with httpx.AsyncClient(verify=False) as client:
        for i in range(0, len(targets), batch):
            chunk = targets[i: i + batch]
            await asyncio.gather(*[fetch_one(client, c) for c in chunk])
            done = sum(
                1 for c in targets[: i + batch]
                if (CACHE_DIR / f"{c['corp_code']}_company_{TODAY}.json").exists()
            )
            print(f"  진행: {min(i+batch, len(targets))}/{len(targets)}  (캐시 저장: {done}개)")
            await asyncio.sleep(3)  # 배치 간 3초 대기 - DART 서버 과부하 방지

    total = sum(
        1 for c in targets
        if (CACHE_DIR / f"{c['corp_code']}_company_{TODAY}.json").exists()
    )
    print(f"\n완료: {total}/{len(targets)} 성공")


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    asyncio.run(main(lim))
