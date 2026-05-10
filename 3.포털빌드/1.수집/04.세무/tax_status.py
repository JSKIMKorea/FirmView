"""
06_fetch_tax.py -- F-08 국세청 사업자상태 조회
공공데이터포털 NTS API (odcloud) -- 사업자등록번호 기반
결과: cache/{corp_code}_tax_{YYYYMMDD}.json

실행: python fetch/06_fetch_tax.py [최대개수]
"""
import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent.parent.parent
CRED = json.loads((ROOT / "credentials.json").read_text(encoding="utf-8"))
NTS_KEY = CRED["odcloud"]["api_key"]
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "06.국세청_사업자상태"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(3)
NTS_URL = "https://api.odcloud.kr/api/nts-businessman/v1/status"

# 사업자상태 코드 → 한글
B_STT_LABEL = {
    "01": "계속사업자",
    "02": "휴업자",
    "03": "폐업자",
}

TAX_TYPE_LABEL = {
    "1": "일반과세자",
    "2": "면세사업자",
    "3": "간이과세자",
}


async def fetch_one(
    client: httpx.AsyncClient, corp_code: str, bizr_no: str
) -> None:
    if not bizr_no:
        return

    b_no = bizr_no.replace("-", "").strip()
    if len(b_no) != 10:
        return

    out = CACHE_DIR / f"{corp_code}_tax_{TODAY}.json"
    if out.exists():
        return

    async with SEM:
        try:
            r = await client.post(
                NTS_URL,
                params={"serviceKey": NTS_KEY},
                json={"b_no": [b_no]},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            data = r.json()
        except Exception as e:
            print(f"  오류 [{corp_code}] {e}")
            return

        if data.get("status_code") == "OK" and data.get("data"):
            raw = data["data"][0]
            result = {
                "corp_code": corp_code,
                "b_no": b_no,
                "b_stt": raw.get("b_stt", ""),
                "b_stt_label": B_STT_LABEL.get(raw.get("b_stt_cd", ""), raw.get("b_stt", "")),
                "tax_type": raw.get("tax_type", ""),
                "tax_type_label": TAX_TYPE_LABEL.get(
                    raw.get("tax_type_cd", ""), raw.get("tax_type", "")
                ),
                "end_dt": raw.get("end_dt", ""),     # 폐업일 (폐업자만)
                "utcc_yn": raw.get("utcc_yn", ""),   # 단위과세전환 여부
                "updated": TODAY,
            }
            out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        await asyncio.sleep(0.1)


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
        bizr_no = data.get("bizr_no", "")
        if bizr_no:
            entries.append((corp_code, bizr_no))

    if limit:
        entries = entries[:limit]

    already = sum(
        1 for cc, _ in entries
        if (CACHE_DIR / f"{cc}_tax_{TODAY}.json").exists()
    )
    print(f"사업자상태 조회 대상: {len(entries)}개 (당일 캐시: {already}개)")

    batch = 50
    async with httpx.AsyncClient(verify=False) as client:
        for i in range(0, len(entries), batch):
            chunk = entries[i: i + batch]
            await asyncio.gather(*[fetch_one(client, cc, bn) for cc, bn in chunk])
            done = sum(
                1 for cc, _ in entries[: i + batch]
                if (CACHE_DIR / f"{cc}_tax_{TODAY}.json").exists()
            )
            print(f"  진행: {min(i+batch, len(entries))}/{len(entries)}  (저장: {done}개)")

    print("완료")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
