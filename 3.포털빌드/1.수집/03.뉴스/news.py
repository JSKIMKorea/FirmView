"""
05_fetch_news.py -- F-09 뉴스 수집
네이버 검색 API -- 회사명으로 최신 뉴스 10건
결과: cache/{corp_code}_news_{YYYYMMDD}.json

주의: 네이버 API 일 25,000건 한도 -- 4,500개 회사 전체 수집 시 2일치 한도 소진
     --listed 플래그로 상장사만 수집하거나 limit 인자 활용 권장

실행: python fetch/05_fetch_news.py [최대개수]
"""
import asyncio
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent.parent.parent
CRED = json.loads((ROOT / "credentials.json").read_text(encoding="utf-8"))
NAVER_ID = CRED["naver"]["client_id"]
NAVER_SEC = CRED["naver"]["client_secret"]
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "05.뉴스"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(3)  # 네이버 API rate limit 준수


def _clean(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).replace("&amp;", "&").replace("&quot;", '"').strip()


async def fetch_one(
    client: httpx.AsyncClient, corp_code: str, corp_name: str
) -> None:
    out = CACHE_DIR / f"{corp_code}_news_{TODAY}.json"
    if out.exists():
        return

    async with SEM:
        try:
            r = await client.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": corp_name, "display": 10, "sort": "date"},
                headers={
                    "X-Naver-Client-Id": NAVER_ID,
                    "X-Naver-Client-Secret": NAVER_SEC,
                },
                timeout=10,
            )
            data = r.json()
        except Exception as e:
            print(f"  오류 [{corp_code}] {e}")
            return

        items = [
            {
                "title": _clean(item.get("title", "")),
                "description": _clean(item.get("description", "")),
                "link": item.get("originallink") or item.get("link", ""),
                "pubDate": item.get("pubDate", ""),
            }
            for item in data.get("items", [])
        ]

        result = {
            "corp_code": corp_code,
            "corp_name": corp_name,
            "items": items,
            "updated": TODAY,
        }
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        await asyncio.sleep(0.05)  # 네이버 API 부하 방지


async def main(limit: int = 0) -> None:
    _pm = os.environ.get("PORTAL_MASTER")
    _allowed: set | None = None
    if _pm:
        _raw = json.loads(Path(_pm).read_text(encoding="utf-8"))
        _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
        _allowed = {c["corp_code"] for c in _comps if c.get("corp_code")}
    entries = []
    for p in sorted(COMPANY_CACHE_DIR.glob("*_company_*.json")):
        corp_code = p.stem.split("_company_")[0]
        if _allowed is not None and corp_code not in _allowed:
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        name = data.get("corp_name", "")
        if name:
            entries.append((corp_code, name))

    if limit:
        entries = entries[:limit]

    already = sum(
        1 for cc, _ in entries
        if (CACHE_DIR / f"{cc}_news_{TODAY}.json").exists()
    )
    print(
        f"뉴스 수집 대상: {len(entries)}개 (당일 캐시: {already}개)"
        f"\n  ※ 네이버 API 일 25,000건 한도 주의 -- 신규 수집: {len(entries)-already}건"
    )

    async with httpx.AsyncClient(verify=False) as client:
        tasks = [fetch_one(client, cc, name) for cc, name in entries]
        batch = 30
        for i in range(0, len(tasks), batch):
            await asyncio.gather(*tasks[i: i + batch])
            done = sum(
                1 for cc, _ in entries[: i + batch]
                if (CACHE_DIR / f"{cc}_news_{TODAY}.json").exists()
            )
            print(f"  진행: {min(i+batch, len(entries))}/{len(entries)}  (저장: {done}개)")
            await asyncio.sleep(0.5)

    print("완료")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
