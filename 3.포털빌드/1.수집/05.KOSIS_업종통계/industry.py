"""
07_fetch_industry.py -- F-04 업종통계 수집 (KOSIS 통계청)
회사의 KSIC 업종코드를 기반으로 동종업계 재무비율 벤치마크 수집
결과: cache/{corp_code}_industry_{YYYYMMDD}.json

KOSIS 기업경영분析 (한국은행 제공):
  - 성장성: 매출액증가율, 영업이익증가율, 순이익증가율
  - 수익성: ROE, ROA, 매출액순이익률, 매출액영업이익률
  - 안정성: 부채비율, 유동비율

실행: python fetch/07_fetch_industry.py [최대개수]
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
KOSIS_KEY = CRED["kosis"]["api_key"]
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "07.업종통계_KOSIS"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"  # 회사 목록 조회용 (다른 스크립트와 공유)
TODAY = date.today().strftime("%Y%m%d")

SEM = asyncio.Semaphore(3)

KOSIS_BASE = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

# KOSIS 한국은행 기업경영분析 -- 업종별 주요재무비율
# orgId=301(통계청)/343(한국은행) + tblId는 KOSIS 통계목록에서 확인
# 주요 테이블 후보 (최신 확인 필요):
#   DT_1KI8015 : 업종별 주요재무비율 (한국은행)
#   DT_1KI8016 : 업종별 성장성비율
KOSIS_TABLES = [
    {"orgId": "301", "tblId": "DT_1KI8015"},  # 업종별 재무비율 (1순위)
    {"orgId": "301", "tblId": "DT_1KI8016"},  # 업종별 성장성 (2순위)
]

# KSIC 대분류(2자리) → KOSIS 업종분류 코드 매핑
# KOSIS 기업경영분析의 업종 분류와 DART KSIC 대분류를 연결
KSIC_TO_KOSIS = {
    "01": "A",  "02": "A",  "03": "A",       # 농림어업
    "05": "B",  "06": "B",  "07": "B",
    "08": "B",  "09": "B",                    # 광업
    "10": "C",  "11": "C",  "12": "C",
    "13": "C",  "14": "C",  "15": "C",
    "16": "C",  "17": "C",  "18": "C",
    "19": "C",  "20": "C",  "21": "C",
    "22": "C",  "23": "C",  "24": "C",
    "25": "C",  "26": "C",  "27": "C",
    "28": "C",  "29": "C",  "30": "C",
    "31": "C",  "32": "C",  "33": "C",       # 제조업
    "35": "D",                                 # 전기/가스
    "36": "E",  "37": "E",  "38": "E",
    "39": "E",                                 # 수도/폐기물
    "41": "F",  "42": "F",  "43": "F",       # 건설업
    "45": "G",  "46": "G",  "47": "G",       # 도소매
    "49": "H",  "50": "H",  "51": "H",
    "52": "H",                                 # 운수/창고
    "55": "I",  "56": "I",                    # 숙박/음식
    "58": "J",  "59": "J",  "60": "J",
    "61": "J",  "62": "J",  "63": "J",       # 정보통신
    "64": "K",  "65": "K",  "66": "K",       # 금융/보험
    "68": "L",                                 # 부동산
    "70": "M",  "71": "M",  "72": "M",
    "73": "M",  "74": "M",  "75": "M",       # 전문/과학기술
    "76": "N",  "77": "N",  "78": "N",
    "79": "N",  "80": "N",  "81": "N",
    "82": "N",                                 # 사업서비스
    "84": "O",                                 # 공공행정
    "85": "P",                                 # 교육
    "86": "Q",  "87": "Q",  "88": "Q",       # 보건/사회복지
    "90": "R",  "91": "R",  "92": "R",
    "93": "R",                                 # 예술/스포츠
    "94": "S",  "95": "S",  "96": "S",       # 협회/기타서비스
}

# 산업별 기본 벤치마크 (KOSIS 데이터 미수집 시 fallback)
# 한국은행 기업경영분析 2023년 기준 평균값 참고
DEFAULT_BENCHMARKS: dict[str, dict] = {
    # === 대분류(섹터 letter) - fallback ===
    "C": {  # 제조업 전반
        "revenue_growth": 3.0, "op_income_growth": 5.0, "ni_growth": 5.0,
        "roe": 7.0, "roa": 4.0, "op_margin": 6.0, "ni_margin": 4.0,
        "debt_ratio": 100.0, "current_ratio": 150.0,
        "label": "제조업",
    },
    "G": {
        "revenue_growth": 3.0, "op_income_growth": 4.0, "ni_growth": 4.0,
        "roe": 8.0, "roa": 3.0, "op_margin": 3.0, "ni_margin": 2.0,
        "debt_ratio": 150.0, "current_ratio": 130.0,
        "label": "도소매업",
    },
    "J": {
        "revenue_growth": 8.0, "op_income_growth": 8.0, "ni_growth": 7.0,
        "roe": 10.0, "roa": 5.0, "op_margin": 10.0, "ni_margin": 7.0,
        "debt_ratio": 80.0, "current_ratio": 170.0,
        "label": "정보통신업",
    },
    "K": {
        "revenue_growth": 5.0, "op_income_growth": 5.0, "ni_growth": 5.0,
        "roe": 9.0, "roa": 1.0, "op_margin": 20.0, "ni_margin": 15.0,
        "debt_ratio": 800.0, "current_ratio": 120.0,
        "label": "금융·보험업",
    },
    "N": {
        "revenue_growth": 5.0, "op_income_growth": 5.0, "ni_growth": 5.0,
        "roe": 8.0, "roa": 3.0, "op_margin": 8.0, "ni_margin": 5.0,
        "debt_ratio": 120.0, "current_ratio": 140.0,
        "label": "사업서비스업",
    },
    "F": {
        "revenue_growth": 4.0, "op_income_growth": 4.0, "ni_growth": 4.0,
        "roe": 9.0, "roa": 4.0, "op_margin": 5.0, "ni_margin": 3.0,
        "debt_ratio": 150.0, "current_ratio": 140.0,
        "label": "건설업",
    },
    "_default": {
        "revenue_growth": 5.0, "op_income_growth": 5.0, "ni_growth": 5.0,
        "roe": 8.0, "roa": 3.5, "op_margin": 7.0, "ni_margin": 4.0,
        "debt_ratio": 120.0, "current_ratio": 140.0,
        "label": "전산업 평균",
    },
}

# === 중분류(KSIC 2자리) - 우선순위 높음 ===
# 한국은행 기업경영분석 2023 중분류 평균 참고. 매칭되면 sector letter보다 우선 적용.
DEFAULT_BENCHMARKS_KSIC2: dict[str, dict] = {
    # 식음료·담배
    "10": {"revenue_growth":3.5,"op_income_growth":4,"ni_growth":4,"roe":7,"roa":4,"op_margin":5,"ni_margin":3.5,"debt_ratio":110,"current_ratio":135,"label":"식료품 제조업"},
    "11": {"revenue_growth":4,"op_income_growth":5,"ni_growth":4,"roe":8,"roa":5,"op_margin":10,"ni_margin":7,"debt_ratio":80,"current_ratio":160,"label":"음료 제조업"},
    "13": {"revenue_growth":2,"op_income_growth":2,"ni_growth":2,"roe":4,"roa":2.5,"op_margin":4,"ni_margin":2,"debt_ratio":140,"current_ratio":130,"label":"섬유제품 제조업"},
    "14": {"revenue_growth":3,"op_income_growth":4,"ni_growth":3,"roe":6,"roa":3.5,"op_margin":5,"ni_margin":3,"debt_ratio":110,"current_ratio":140,"label":"의복 제조업"},
    "17": {"revenue_growth":2,"op_income_growth":3,"ni_growth":3,"roe":5,"roa":3,"op_margin":4,"ni_margin":2.5,"debt_ratio":120,"current_ratio":135,"label":"펄프·종이 제조업"},
    "19": {"revenue_growth":4,"op_income_growth":6,"ni_growth":6,"roe":10,"roa":5,"op_margin":4,"ni_margin":3,"debt_ratio":150,"current_ratio":140,"label":"코크스·석유정제품 제조업"},
    "20": {"revenue_growth":3,"op_income_growth":5,"ni_growth":5,"roe":8,"roa":4.5,"op_margin":7,"ni_margin":5,"debt_ratio":100,"current_ratio":150,"label":"화학제품 제조업"},
    "21": {"revenue_growth":6,"op_income_growth":7,"ni_growth":7,"roe":10,"roa":6,"op_margin":12,"ni_margin":9,"debt_ratio":80,"current_ratio":180,"label":"의약품 제조업"},
    "22": {"revenue_growth":3,"op_income_growth":4,"ni_growth":4,"roe":7,"roa":4,"op_margin":6,"ni_margin":4,"debt_ratio":110,"current_ratio":140,"label":"고무·플라스틱 제조업"},
    "23": {"revenue_growth":2.5,"op_income_growth":3,"ni_growth":3,"roe":6,"roa":3.5,"op_margin":6,"ni_margin":4,"debt_ratio":120,"current_ratio":140,"label":"비금속 광물제품 제조업"},
    "24": {"revenue_growth":3,"op_income_growth":4,"ni_growth":4,"roe":6,"roa":3.5,"op_margin":5,"ni_margin":3,"debt_ratio":140,"current_ratio":130,"label":"1차 금속 제조업"},
    "25": {"revenue_growth":3.5,"op_income_growth":5,"ni_growth":5,"roe":7.5,"roa":4,"op_margin":6,"ni_margin":4,"debt_ratio":110,"current_ratio":140,"label":"금속가공제품 제조업"},
    "26": {"revenue_growth":5,"op_income_growth":7,"ni_growth":7,"roe":9,"roa":5.5,"op_margin":8,"ni_margin":6,"debt_ratio":90,"current_ratio":170,"label":"전자부품·컴퓨터·통신장비 제조업"},
    "27": {"revenue_growth":5,"op_income_growth":6,"ni_growth":6,"roe":9,"roa":6,"op_margin":9,"ni_margin":7,"debt_ratio":85,"current_ratio":175,"label":"의료·정밀·광학기기 제조업"},
    "28": {"revenue_growth":4,"op_income_growth":5,"ni_growth":5,"roe":8,"roa":4.5,"op_margin":7,"ni_margin":5,"debt_ratio":100,"current_ratio":150,"label":"전기장비 제조업"},
    "29": {"revenue_growth":3.5,"op_income_growth":5,"ni_growth":5,"roe":8,"roa":4,"op_margin":6,"ni_margin":4,"debt_ratio":110,"current_ratio":145,"label":"기타 기계·장비 제조업"},
    "30": {"revenue_growth":4,"op_income_growth":5,"ni_growth":5,"roe":7,"roa":4,"op_margin":5,"ni_margin":3.5,"debt_ratio":130,"current_ratio":135,"label":"자동차·트레일러 제조업"},
    "31": {"revenue_growth":4,"op_income_growth":5,"ni_growth":5,"roe":7,"roa":4,"op_margin":5,"ni_margin":3.5,"debt_ratio":130,"current_ratio":135,"label":"기타 운송장비 제조업 (조선·항공)"},
    "32": {"revenue_growth":2.5,"op_income_growth":3,"ni_growth":3,"roe":6,"roa":3.5,"op_margin":5,"ni_margin":3,"debt_ratio":110,"current_ratio":140,"label":"가구 제조업"},
    "33": {"revenue_growth":3,"op_income_growth":3,"ni_growth":3,"roe":6,"roa":3.5,"op_margin":5,"ni_margin":3,"debt_ratio":110,"current_ratio":140,"label":"기타 제품 제조업"},

    # 건설
    "41": {"revenue_growth":4,"op_income_growth":4,"ni_growth":4,"roe":9,"roa":4,"op_margin":5,"ni_margin":3,"debt_ratio":150,"current_ratio":140,"label":"종합 건설업"},
    "42": {"revenue_growth":4,"op_income_growth":4,"ni_growth":4,"roe":10,"roa":5,"op_margin":6,"ni_margin":4,"debt_ratio":140,"current_ratio":140,"label":"전문직별 공사업"},

    # 도소매·운수
    "45": {"revenue_growth":3,"op_income_growth":3,"ni_growth":3,"roe":7,"roa":2.5,"op_margin":3,"ni_margin":2,"debt_ratio":160,"current_ratio":125,"label":"자동차 판매업"},
    "46": {"revenue_growth":3,"op_income_growth":3.5,"ni_growth":3.5,"roe":8,"roa":3,"op_margin":3,"ni_margin":2,"debt_ratio":150,"current_ratio":130,"label":"도매업"},
    "47": {"revenue_growth":3,"op_income_growth":3.5,"ni_growth":3.5,"roe":7,"roa":3,"op_margin":3.5,"ni_margin":2.5,"debt_ratio":140,"current_ratio":130,"label":"소매업"},
    "49": {"revenue_growth":4,"op_income_growth":5,"ni_growth":5,"roe":7,"roa":3.5,"op_margin":6,"ni_margin":4,"debt_ratio":130,"current_ratio":135,"label":"육상운송업"},
    "50": {"revenue_growth":5,"op_income_growth":6,"ni_growth":6,"roe":8,"roa":3.5,"op_margin":7,"ni_margin":5,"debt_ratio":140,"current_ratio":135,"label":"수상운송업"},
    "51": {"revenue_growth":6,"op_income_growth":7,"ni_growth":7,"roe":9,"roa":4,"op_margin":8,"ni_margin":6,"debt_ratio":140,"current_ratio":140,"label":"항공운송업"},
    "52": {"revenue_growth":4,"op_income_growth":5,"ni_growth":5,"roe":8,"roa":4,"op_margin":7,"ni_margin":5,"debt_ratio":110,"current_ratio":145,"label":"창고·운송관련 서비스업"},

    # 정보통신
    "58": {"revenue_growth":7,"op_income_growth":8,"ni_growth":7,"roe":10,"roa":6,"op_margin":12,"ni_margin":9,"debt_ratio":70,"current_ratio":180,"label":"출판업 (소프트웨어 포함)"},
    "59": {"revenue_growth":6,"op_income_growth":7,"ni_growth":6,"roe":9,"roa":5,"op_margin":10,"ni_margin":7,"debt_ratio":85,"current_ratio":160,"label":"영상·오디오 제작·배급업"},
    "60": {"revenue_growth":3,"op_income_growth":3,"ni_growth":3,"roe":6,"roa":3,"op_margin":5,"ni_margin":3,"debt_ratio":120,"current_ratio":140,"label":"방송업"},
    "61": {"revenue_growth":3,"op_income_growth":4,"ni_growth":4,"roe":8,"roa":4,"op_margin":12,"ni_margin":8,"debt_ratio":100,"current_ratio":150,"label":"통신업"},
    "62": {"revenue_growth":9,"op_income_growth":9,"ni_growth":8,"roe":12,"roa":7,"op_margin":11,"ni_margin":8,"debt_ratio":70,"current_ratio":190,"label":"컴퓨터 프로그래밍·SI"},
    "63": {"revenue_growth":8,"op_income_growth":9,"ni_growth":8,"roe":11,"roa":6,"op_margin":10,"ni_margin":7,"debt_ratio":75,"current_ratio":180,"label":"정보서비스업"},

    # 금융·보험
    "64": {"revenue_growth":5,"op_income_growth":5,"ni_growth":5,"roe":8,"roa":1,"op_margin":25,"ni_margin":18,"debt_ratio":900,"current_ratio":110,"label":"금융업 (지주·여신 등)"},
    "65": {"revenue_growth":4,"op_income_growth":5,"ni_growth":5,"roe":9,"roa":1.2,"op_margin":18,"ni_margin":13,"debt_ratio":750,"current_ratio":115,"label":"보험·연금업"},
    "66": {"revenue_growth":5,"op_income_growth":6,"ni_growth":6,"roe":10,"roa":4,"op_margin":15,"ni_margin":11,"debt_ratio":300,"current_ratio":130,"label":"금융·보험 관련 서비스업"},

    # 부동산·전문서비스
    "68": {"revenue_growth":2,"op_income_growth":2,"ni_growth":2,"roe":5,"roa":2.5,"op_margin":15,"ni_margin":10,"debt_ratio":180,"current_ratio":120,"label":"부동산업"},
    "70": {"revenue_growth":4,"op_income_growth":4,"ni_growth":4,"roe":9,"roa":4,"op_margin":7,"ni_margin":5,"debt_ratio":110,"current_ratio":145,"label":"건축기술·엔지니어링 서비스업"},
    "71": {"revenue_growth":4,"op_income_growth":4,"ni_growth":4,"roe":10,"roa":4.5,"op_margin":7,"ni_margin":5,"debt_ratio":110,"current_ratio":145,"label":"전문 서비스업"},
    "72": {"revenue_growth":4,"op_income_growth":4.5,"ni_growth":4.5,"roe":10,"roa":4,"op_margin":6,"ni_margin":4,"debt_ratio":120,"current_ratio":140,"label":"사업시설 관리·지원 서비스업"},
    "73": {"revenue_growth":4,"op_income_growth":4,"ni_growth":4,"roe":9,"roa":4,"op_margin":5,"ni_margin":3,"debt_ratio":120,"current_ratio":140,"label":"사업 지원 서비스업"},
    "74": {"revenue_growth":5,"op_income_growth":5,"ni_growth":5,"roe":8,"roa":4,"op_margin":7,"ni_margin":5,"debt_ratio":100,"current_ratio":150,"label":"연구개발업"},
    "75": {"revenue_growth":5,"op_income_growth":6,"ni_growth":6,"roe":9,"roa":3,"op_margin":15,"ni_margin":10,"debt_ratio":150,"current_ratio":130,"label":"임대업"},
    "76": {"revenue_growth":5,"op_income_growth":5,"ni_growth":5,"roe":8,"roa":3,"op_margin":8,"ni_margin":5,"debt_ratio":120,"current_ratio":140,"label":"임대업"},

    # 숙박·음식·교육·기타
    "55": {"revenue_growth":3,"op_income_growth":3,"ni_growth":3,"roe":6,"roa":3,"op_margin":7,"ni_margin":4,"debt_ratio":150,"current_ratio":120,"label":"숙박업"},
    "56": {"revenue_growth":3,"op_income_growth":3,"ni_growth":3,"roe":7,"roa":3,"op_margin":4,"ni_margin":2.5,"debt_ratio":140,"current_ratio":120,"label":"음식점·주점업"},
    "85": {"revenue_growth":3,"op_income_growth":3,"ni_growth":3,"roe":6,"roa":3,"op_margin":5,"ni_margin":3,"debt_ratio":110,"current_ratio":140,"label":"교육 서비스업"},
    "86": {"revenue_growth":4,"op_income_growth":4,"ni_growth":4,"roe":8,"roa":4,"op_margin":6,"ni_margin":4,"debt_ratio":120,"current_ratio":145,"label":"보건업"},
    "87": {"revenue_growth":3,"op_income_growth":3,"ni_growth":3,"roe":5,"roa":2.5,"op_margin":4,"ni_margin":2.5,"debt_ratio":110,"current_ratio":140,"label":"사회복지 서비스업"},
}


def get_default_benchmark(induty_code: str) -> dict:
    """KSIC 코드로 기본 벤치마크 반환.
    우선순위: ① 2자리 KSIC 중분류 매칭 → ② 대분류(섹터 letter) → ③ 전산업 평균"""
    if not induty_code:
        return DEFAULT_BENCHMARKS["_default"]
    prefix2 = induty_code[:2]
    # 1순위: 2자리 KSIC 중분류
    if prefix2 in DEFAULT_BENCHMARKS_KSIC2:
        return DEFAULT_BENCHMARKS_KSIC2[prefix2]
    # 2순위: 대분류 letter
    kosis_cls = KSIC_TO_KOSIS.get(prefix2, "")
    return DEFAULT_BENCHMARKS.get(kosis_cls, DEFAULT_BENCHMARKS["_default"])


async def fetch_kosis_industry(
    client: httpx.AsyncClient, induty_code: str
) -> dict | None:
    """KOSIS API로 업종별 재무비율 조회"""
    ksic_prefix = induty_code[:2] if induty_code else ""
    kosis_cls = KSIC_TO_KOSIS.get(ksic_prefix, "")
    if not kosis_cls:
        return None

    for tbl in KOSIS_TABLES:
        try:
            r = await client.get(
                KOSIS_BASE,
                params={
                    "method": "getList",
                    "apiKey": KOSIS_KEY,
                    "itmId": "ALL",
                    "objL1": kosis_cls,
                    "format": "json",
                    "jsonVD": "Y",
                    "prdSe": "Y",
                    "startPrdDe": "2021",
                    "endPrdDe": "2024",
                    "orgId": tbl["orgId"],
                    "tblId": tbl["tblId"],
                },
                timeout=20,
            )
            data = r.json()
            if isinstance(data, list) and data:
                return {"source": "kosis", "table": tbl["tblId"], "data": data}
        except Exception:
            continue
    return None


async def fetch_one(
    client: httpx.AsyncClient, corp_code: str, induty_code: str
) -> None:
    out = CACHE_DIR / f"{corp_code}_industry_{TODAY}.json"
    if out.exists():
        return

    async with SEM:
        # KOSIS API 시도
        kosis_data = await fetch_kosis_industry(client, induty_code)

        # fallback: 기본 벤치마크
        benchmark = get_default_benchmark(induty_code)
        result = {
            "corp_code": corp_code,
            "induty_code": induty_code,
            "kosis_cls": KSIC_TO_KOSIS.get(induty_code[:2] if induty_code else "", ""),
            "benchmark": benchmark,
            "kosis_raw": kosis_data,
            "updated": TODAY,
        }
        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )


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
        induty_code = data.get("induty_code", "")
        entries.append((corp_code, induty_code))

    if limit:
        entries = entries[:limit]

    already = sum(
        1 for cc, _ in entries
        if (CACHE_DIR / f"{cc}_industry_{TODAY}.json").exists()
    )
    print(f"업종통계 수집 대상: {len(entries)}개 (당일 캐시: {already}개)")

    async with httpx.AsyncClient(verify=False) as client:
        tasks = [fetch_one(client, cc, ic) for cc, ic in entries]
        batch = 50
        for i in range(0, len(tasks), batch):
            await asyncio.gather(*tasks[i: i + batch])
            done = sum(
                1 for cc, _ in entries[: i + batch]
                if (CACHE_DIR / f"{cc}_industry_{TODAY}.json").exists()
            )
            print(f"  진행: {min(i+batch, len(entries))}/{len(entries)}  (저장: {done}개)")

    print("완료")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
