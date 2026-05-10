"""
10_fetch_tax_investigation.py -- 세무조사 관련 공개 정보 수집 (참고용)

세무조사 정보는 국세기본법 §81-13(과세정보 비밀유지)에 따라 공개 API로 제공되지 않으므로
다음 두 우회 경로로 *공개된 흔적*만 수집한다:

  (1) DART 공시 - 회사가 자발적으로 신고한 「세무조사·추징·조세소송」 관련 공시
      (상장사·외감대상 한정, 자율 공시이므로 누락 가능)
  (2) 네이버 뉴스 - "{회사명} 세무조사" 쿼리 별도 검색 (보도된 건만)

결과: cache/{corp_code}_tax_invest_{YYYYMMDD}.json
실행: python fetch/10_fetch_tax_investigation.py [최대개수]
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
DART_KEY = CRED["dart"]["api_key"]
NAVER_ID = CRED["naver"]["client_id"]
NAVER_SEC = CRED["naver"]["client_secret"]
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "10.세무조사이력"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
TODAY = date.today().strftime("%Y%m%d")

SEM_DART = asyncio.Semaphore(5)
SEM_NAVER = asyncio.Semaphore(3)

# DART 공시 보고서명 매칭 - 세무조사·추징·조세 분쟁 관련
TAX_KEYWORDS_RE = re.compile(
    r"세무조사|추징세|추징금|조세\s?(소송|불복|심판)|과세처분|과세전적부심사|국세\s?부과|법인세\s?부과|가산세\s?부과|세금\s?(추징|부과)"
)

# 뉴스 제목 관련성 필터 - 세무조사 키워드가 제목에 있어야 함
NEWS_TITLE_TAX_RE = re.compile(
    r"세무조사|추징|과세처분|국세청\s?(조사|부과|처분)|세무당국|탈세|조세포탈|세금\s?부과|가산세"
)

# 법인 형태 접미사 패턴 (별칭 생성 시 제거)
_CORP_SUFFIX_RE = re.compile(r"\s*\(주\)|\s*\(유\)|\s*\(사\)|\s*\(합\)|^주식회사\s*|\s*주식회사$")


def _name_aliases(corp_name: str, stock_name: str = "") -> list[str]:
    """매칭에 사용할 회사명 별칭 목록 생성.
    예: '삼성에스디에스(주)', '삼성에스디에스' → ['삼성에스디에스(주)', '삼성에스디에스']
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(name: str) -> None:
        for s in [name, _CORP_SUFFIX_RE.sub("", name).strip()]:
            if s and s not in seen:
                seen.add(s)
                result.append(s)

    _add(corp_name)
    if stock_name:
        _add(stock_name)
    return result


def _matches_corp(text: str, aliases: list[str]) -> bool:
    return any(alias in text for alias in aliases)


async def _fetch_dart(client: httpx.AsyncClient, corp_code: str) -> list:
    """최근 3년치 DART 공시에서 세무조사 키워드 매칭 보고서만 추출"""
    bgn = f"{date.today().year - 3}0101"
    items: list = []
    async with SEM_DART:
        try:
            r = await client.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": DART_KEY,
                    "corp_code": corp_code,
                    "bgn_de": bgn,
                    "end_de": TODAY,
                    "page_count": 100,
                    "sort": "date",
                    "sort_mth": "desc",
                },
                timeout=20,
            )
            d = r.json()
        except Exception:
            return []

        if d.get("status") != "000" or not d.get("list"):
            return []

        for it in d["list"]:
            nm = (it.get("report_nm") or "").strip()
            if not TAX_KEYWORDS_RE.search(nm):
                continue
            rcept_no = it.get("rcept_no", "")
            if not rcept_no:
                continue
            items.append({
                "rcept_no": rcept_no,
                "rcept_dt": it.get("rcept_dt", ""),
                "report_nm": nm,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
            })
    return items


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").replace("&amp;", "&").replace("&quot;", '"').strip()


async def _fetch_naver(
    client: httpx.AsyncClient,
    corp_name: str,
    stock_name: str = "",
    display: int = 10,
) -> list:
    """네이버 뉴스에서 '{회사명} 세무조사' 검색 후 제목 관련성 필터링
    - 제목에 세무조사 키워드 필수 (주가·일반 뉴스 오염 방지)
    - 제목 또는 설명에 회사명 별칭 중 하나 이상 포함 필수
    - 별칭: corp_name + stock_name + 법인 접미사 제거형 (유도리 있는 매칭)
    """
    if not corp_name:
        return []
    async with SEM_NAVER:
        try:
            r = await client.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": f"{corp_name} 세무조사", "display": display, "sort": "date"},
                headers={
                    "X-Naver-Client-Id": NAVER_ID,
                    "X-Naver-Client-Secret": NAVER_SEC,
                },
                timeout=10,
            )
            data = r.json()
        except Exception:
            return []

        aliases = _name_aliases(corp_name, stock_name)
        results = []
        for item in data.get("items", []):
            title = _strip_html(item.get("title", ""))
            desc  = _strip_html(item.get("description", ""))
            # ① 제목에 세무조사 키워드 없으면 제외
            if not NEWS_TITLE_TAX_RE.search(title):
                continue
            # ② 제목 + 설명에 회사명 별칭(법인형 포함/제외, stock_name 포함) 중 하나도 없으면 제외
            if aliases and not _matches_corp(title + " " + desc, aliases):
                continue
            results.append({
                "title": title,
                "description": desc,
                "link": item.get("originallink") or item.get("link", ""),
                "pubDate": item.get("pubDate", ""),
            })
        return results


async def fetch_one(
    client: httpx.AsyncClient, corp_code: str, corp_name: str, stock_name: str = ""
) -> None:
    out = CACHE_DIR / f"{corp_code}_tax_invest_{TODAY}.json"
    if out.exists():
        return

    dart_items, news_items = await asyncio.gather(
        _fetch_dart(client, corp_code),
        _fetch_naver(client, corp_name, stock_name),
    )

    result = {
        "corp_code":  corp_code,
        "corp_name":  corp_name,
        "stock_name": stock_name,
        "dart_items": dart_items,
        "news_items": news_items,
        "checked_at": TODAY,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


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
        d = json.loads(p.read_text(encoding="utf-8"))
        name = d.get("corp_name", "")
        stock = d.get("stock_name", "")
        if name:
            entries.append((corp_code, name, stock))

    if limit:
        entries = entries[:limit]

    already = sum(
        1 for cc, *_ in entries
        if (CACHE_DIR / f"{cc}_tax_invest_{TODAY}.json").exists()
    )
    print(
        f"세무조사 흔적 수집 대상: {len(entries)}개 (당일 캐시: {already}개)"
        f"\n  ※ 네이버 + DART 호출 - 신규 수집: {len(entries)-already}건"
    )

    async with httpx.AsyncClient(verify=False) as client:
        batch = 30
        for i in range(0, len(entries), batch):
            chunk = entries[i: i + batch]
            await asyncio.gather(*[fetch_one(client, cc, nm, st) for cc, nm, st in chunk])
            done = sum(
                1 for cc, _ in entries[: i + batch]
                if (CACHE_DIR / f"{cc}_tax_invest_{TODAY}.json").exists()
            )
            print(f"  진행: {min(i+batch, len(entries))}/{len(entries)}  (저장: {done}개)")
            await asyncio.sleep(0.3)

    print("완료")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
