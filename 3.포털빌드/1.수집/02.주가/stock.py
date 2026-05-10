"""
03_fetch_stock.py -- F-05 주가/PBR 수집
상장사: yfinance(KRX) 우선 → 금융위원회 API
비상장사: 상속·증여세법 보충적 평가 (financial 캐시 기반 계산)
결과: cache/{corp_code}_stock_{YYYYMMDD}.json

발행주식수 취득 우선순위:
  1. DART company.json isu_shr
  2. DART stockTotqySttus.json istc_totqy (합계 기준)
  ※ majorstock.json ctr_stkqy는 기준주식수(≠총발행주식수)로 사용 불가

실행: python fetch/03_fetch_stock.py [최대개수]
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Windows 콘솔 UTF-8 즉시 출력
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
else:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

import httpx

ROOT = Path(__file__).parent.parent.parent.parent
CRED = json.loads((ROOT / "credentials.json").read_text(encoding="utf-8"))
DART_KEY = CRED["dart"]["api_key"]
FSC_KEY = CRED["odcloud"]["api_key"]
FSC_BASE = CRED.get("fsc_stock", {}).get(
    "base_url",
    "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService",
)
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR  = CACHE_ROOT / "03.주가_BPS_PBR"
COMPANY_CACHE_DIR = CACHE_ROOT / "01.기업개황"
TODAY = date.today().strftime("%Y%m%d")
_WEEK_AGO = (date.today() - timedelta(days=7)).strftime("%Y%m%d")

# KRX OpenAPI (data-dbg.krx.co.kr) — GET + AUTH_KEY 헤더 방식
# 이용신청 필요: openapi.krx.co.kr → 마이페이지 → API 이용신청 → stk_bydd_trd / stk_isu_base_info
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
KRX_KEY  = CRED.get("krx", {}).get("api_key", "")

SEM = asyncio.Semaphore(3)

_CAPITAL_NMS = {"자본총계", "자본", "순자산"}
_NI_NMS = {"당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"}


def _latest_cache(prefix: str) -> dict | None:
    """prefix 끝 단어로 서브폴더 라우팅 (예: corp_code_company → 01.기업개황)"""
    if prefix.endswith("_company"):
        sub = CACHE_ROOT / "01.기업개황"
    elif prefix.endswith("_financial"):
        sub = CACHE_ROOT / "02.재무제표_감사의견"
    else:
        sub = CACHE_DIR
    files = sorted(sub.glob(f"{prefix}_*.json"), reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))


def _find_account(fs_list: list, names: set) -> float | None:
    for row in fs_list:
        if row.get("account_nm") in names:
            val_str = str(row.get("thstrm_amount", "")).replace(",", "").strip()
            if val_str and val_str not in ("", "-"):
                try:
                    return float(val_str)
                except ValueError:
                    pass
    return None


async def fetch_shares_from_dart(client: httpx.AsyncClient, corp_code: str) -> int:
    """DART 주식총수현황(stockTotqySttus)에서 현재 발행 총주식수(합계) 조회.
    합계 행이 가장 큰 값이므로 모든 행의 istc_totqy 최댓값을 반환."""
    for reprt_code in ("11011",):  # 사업보고서 기준
        for bsns_year in ("2024", "2023", "2022"):
            try:
                r = await client.get(
                    "https://opendart.fss.or.kr/api/stockTotqySttus.json",
                    params={
                        "crtfc_key": DART_KEY,
                        "corp_code": corp_code,
                        "bsns_year": bsns_year,
                        "reprt_code": reprt_code,
                    },
                    timeout=20,
                )
                d = r.json()
                if d.get("status") == "000" and d.get("list"):
                    best = 0
                    for item in d["list"]:
                        val_str = str(item.get("istc_totqy", "")).replace(",", "").strip()
                        if val_str and val_str not in ("", "-"):
                            try:
                                v = int(float(val_str))
                                if v > best:
                                    best = v
                            except (ValueError, TypeError):
                                pass
                    if best > 0:
                        return best
            except Exception:
                pass
    return 0


def calc_unlisted(corp_code: str, shares_hint: int = 0) -> dict | None:
    """상증세법 보충적 평가 -- 비상장사 주가 추정.
    shares_hint: stockTotqySttus 등 외부에서 구한 발행주식수 (0이면 무시)."""
    fin = _latest_cache(f"{corp_code}_financial")
    comp = _latest_cache(f"{corp_code}_company")
    if not fin or not comp:
        return None

    # 발행주식수: isu_shr → shares_hint 순
    shares = 0
    try:
        shares = int(str(comp.get("isu_shr", "0")).replace(",", ""))
    except ValueError:
        pass
    if not shares and shares_hint > 0:
        shares = shares_hint

    if not shares:
        return None

    # 별도(OFS) 기준 -- 최신 연도 자본총계 + 최근 3년 당기순이익
    net_assets = None
    eps_data = []

    for year_str in sorted(fin.get("years", {}).keys(), reverse=True):
        ofs = fin["years"][year_str].get("fs", {}).get("OFS", [])
        if not ofs:
            continue
        if net_assets is None:
            net_assets = _find_account(ofs, _CAPITAL_NMS)
        ni = _find_account(ofs, _NI_NMS)
        if ni is not None:
            eps_data.append({"year": int(year_str), "eps": ni / shares})
        if len(eps_data) >= 3:
            break

    if not net_assets or len(eps_data) < 2:
        return {
            "source": "unlisted_calc",
            "price": None,
            "label": "산출 불가 (재무 데이터 부족)",
            "warning": "최소 2개 사업연도 필요",
        }

    net_asset_value = net_assets / shares
    n = len(eps_data[:3])
    years_used = [d["year"] for d in eps_data[:n]]

    if n >= 3:
        weighted_eps = (
            eps_data[0]["eps"] * 3 + eps_data[1]["eps"] * 2 + eps_data[2]["eps"] * 1
        ) / 6
        warning = None
    else:
        weighted_eps = (eps_data[0]["eps"] * 3 + eps_data[1]["eps"] * 2) / 5
        warning = "전전년 재무 데이터 미공시 -- 2개년 가중평균 적용"

    income_value = weighted_eps / 0.10
    price = (net_asset_value * 2 + income_value * 3) / 5
    year_label = "·".join(str(y) for y in years_used)

    bps = round(net_asset_value)
    latest_year = years_used[0]

    return {
        "source": "unlisted_calc",
        "price": round(price),
        "net_asset_value": round(net_asset_value),
        "income_value": round(income_value),
        "bps": bps,
        "bps_label": f"({latest_year}년 별도 기준)",
        "years_used": years_used,
        "label": f"({year_label}년 자료 기준)",
        "warning": warning,
    }


def _make_yf_session():
    """curl_cffi로 브라우저 TLS 흉내 — 대량 요청 시 Yahoo Finance 차단 방지."""
    try:
        from curl_cffi import requests as cffi_req
        return cffi_req.Session(impersonate="chrome")
    except ImportError:
        return None


_YF_SESSION = _make_yf_session()


def fetch_yfinance(stock_code: str) -> dict | None:
    """현재 종가 + 최근 12개월 월말 종가 시계열 동시 수집.
    curl_cffi 세션 사용 시 대량 요청 차단 방지."""
    try:
        import yfinance as yf

        ticker = f"{stock_code}.KS"
        tk = yf.Ticker(ticker, session=_YF_SESSION) if _YF_SESSION else yf.Ticker(ticker)
        info = tk.fast_info
        price = getattr(info, "last_price", None)
        if not price or price <= 0:
            return None

        # 최근 12개월 월별 종가 (시계열)
        history = []
        try:
            df = tk.history(period="13mo", interval="1mo", auto_adjust=False)
            if df is not None and not df.empty:
                for idx, row in df.iterrows():
                    close = float(row.get("Close", 0) or 0)
                    if close > 0:
                        history.append({
                            "date": idx.strftime("%Y-%m"),
                            "close": round(close),
                        })
                history = history[-13:]  # 최근 13개 (현재 월 포함)
        except Exception:
            pass

        result = {
            "source": "yfinance",
            "price": round(price),
            "ticker": ticker,
            "date": TODAY,
            "history": history,
        }

        # 확장 정보: 목표주가 / 투자의견 / 배당 (best-effort)
        try:
            inf = tk.info
            tp = inf.get("targetMeanPrice")
            if tp:
                result["target_mean_price"] = round(tp)
            ac = inf.get("numberOfAnalystOpinions")
            if ac:
                result["analyst_count"] = int(ac)
            rk = inf.get("recommendationKey")
            if rk:
                result["recommendation_key"] = rk
            dy = inf.get("dividendYield")
            if dy is not None:
                result["dividend_yield"] = round(float(dy), 2)
            dr = inf.get("dividendRate")
            if dr:
                result["dividend_rate"] = round(float(dr))
            ex_ts = inf.get("exDividendDate")
            if ex_ts:
                from datetime import datetime as _dt
                result["ex_dividend_date"] = _dt.fromtimestamp(int(ex_ts)).strftime("%Y%m%d")
        except Exception:
            pass

        return result
    except Exception:
        pass
    return None


async def fetch_fsc(client: httpx.AsyncClient, stock_code: str) -> dict | None:
    """금융위원회 주식시세 API (전일 기준)"""
    try:
        r = await client.get(
            f"{FSC_BASE}/getStockPriceInfo",
            params={
                "serviceKey": FSC_KEY,
                "likeSrtnCd": stock_code,
                "numOfRows": 1,
                "pageNo": 1,
                "resultType": "json",
            },
            timeout=15,
        )
        body = r.json()
        items = (
            body.get("response", {})
            .get("body", {})
            .get("items", {})
            .get("item", [])
        )
        if items:
            item = items[0] if isinstance(items, list) else items
            price = int(str(item.get("clpr", "0")).replace(",", ""))
            if price > 0:
                return {
                    "source": "fsc_stock",
                    "price": price,
                    "date": item.get("basDt", TODAY),
                    "ticker": stock_code,
                }
    except Exception:
        pass
    return None


async def fetch_krx_price(client: httpx.AsyncClient, stock_code: str, market: str) -> dict | None:
    """KRX OpenAPI 일별매매정보 — GET + AUTH_KEY 헤더 방식.
    API 이용신청 후 사용 가능: openapi.krx.co.kr → stk_bydd_trd / ksq_bydd_trd
    반환: price(종가), list_shrs(상장주식수), mktcap(시가총액)"""
    if not KRX_KEY:
        return None
    api_id = "stk_bydd_trd" if market in ("Y", "N") else "ksq_bydd_trd"
    try:
        r = await client.get(
            f"{KRX_BASE}/{api_id}",
            params={"basDd": TODAY},
            headers={"AUTH_KEY": KRX_KEY},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        rows = r.json().get("OutBlock_1", [])
        row = next((x for x in rows if x.get("ISU_SRT_CD") == stock_code), None)
        if not row:
            return None
        price_str = str(row.get("TDD_CLSPRC", "")).replace(",", "")
        shrs_str  = str(row.get("LIST_SHRS",  "")).replace(",", "")
        cap_str   = str(row.get("MKTCAP",     "")).replace(",", "")
        price = int(price_str) if price_str.isdigit() else None
        shrs  = int(shrs_str)  if shrs_str.isdigit()  else None
        cap   = int(cap_str)   if cap_str.isdigit()   else None
        if price is None:
            return None
        return {
            "source":      "krx_api",
            "price":       price,
            "list_shrs":   shrs,
            "mktcap":      cap,
            "date":        TODAY,
            "ticker":      stock_code,
        }
    except Exception:
        return None


async def process_one(
    client: httpx.AsyncClient, corp_code: str, stock_code: str, corp_cls: str
) -> None:
    out = CACHE_DIR / f"{corp_code}_stock_{TODAY}.json"
    if out.exists():
        return

    async with SEM:
        result = None
        is_listed = corp_cls in ("Y", "K", "N") and bool(stock_code)

        # --- 발행주식수 사전 취득 (3단계 fallback) ---
        shares = 0
        shares_source = ""
        comp = _latest_cache(f"{corp_code}_company")
        if comp:
            try:
                shares = int(str(comp.get("isu_shr", "0")).replace(",", ""))
                if shares:
                    shares_source = "DART 기업개황 (isu_shr)"
            except ValueError:
                pass

        if not shares:
            shares = await fetch_shares_from_dart(client, corp_code)
            if shares:
                shares_source = "DART 주식총수현황 (stockTotqySttus)"

        # --- 주가 취득 ---
        if is_listed:
            # KRX OpenAPI (이용신청 후 활성화) → yfinance → 금융위 순
            result = await fetch_krx_price(client, stock_code, corp_cls)
            if not result:
                result = fetch_yfinance(stock_code)
            if not result:
                result = await fetch_fsc(client, stock_code)

        # 상장 주가 미수집 or 비상장 → 추정주가 (발행주식수 hint 전달)
        if not result:
            result = calc_unlisted(corp_code, shares_hint=shares)
            if result and is_listed:
                result["note"] = "실시간 주가 조회 실패 -- 추정값"

        # --- BPS 계산 (상장사 포함, 아직 bps 없는 경우) ---
        if result and "bps" not in result and shares:
            fin = _latest_cache(f"{corp_code}_financial")
            if fin:
                latest_year_str = sorted(fin.get("years", {}).keys(), reverse=True)
                if latest_year_str:
                    yr = latest_year_str[0]
                    ofs = fin["years"][yr].get("fs", {}).get("OFS", [])
                    na = _find_account(ofs, _CAPITAL_NMS)
                    if na:
                        bps_candidate = round(na / shares)
                        if bps_candidate <= 100_000_000:
                            result["bps"] = bps_candidate
                            result["bps_label"] = f"({yr}년 별도 기준)"
                            # BPS 산출에 사용된 원시 값 저장 (UI 표시용)
                            result["bps_capital_total"] = int(na)
                            result["bps_shares"] = shares
                            result["bps_shares_source"] = shares_source
                            result["bps_year"] = yr
                        else:
                            result["bps"] = None
                            result["bps_label"] = "산출 불가 (주식수 데이터 오류)"

        # 발행주식수 정보는 상장사도 항상 저장 (UI에서 활용)
        if result and shares and "shares_used" not in result:
            result["shares_used"] = shares
            result["shares_source"] = shares_source

        # --- PBR ---
        if result and result.get("price") and result.get("bps"):
            p, b = result["price"], result["bps"]
            result["pbr"] = round(p / b, 2) if b else None

        if result:
            out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )


async def main(limit: int = 0) -> None:
    print("[1/3] 회사 목록 로드 중...", flush=True)
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
        entries.append(
            (corp_code, data.get("stock_code", "") or "", data.get("corp_cls", "E"))
        )

    if limit:
        # 테스트 시 상장사(stock_code 있는) 우선 선택
        listed   = [(cc, sc, cls) for cc, sc, cls in entries if cls in ("Y", "K", "N") and sc]
        unlisted = [(cc, sc, cls) for cc, sc, cls in entries if not (cls in ("Y", "K", "N") and sc)]
        entries  = (listed + unlisted)[:limit]

    print(f"[2/3] 주가 수집 대상: {len(entries)}개 (KRX API키: {'OK' if KRX_KEY else '없음'})", flush=True)
    async with httpx.AsyncClient(verify=False) as client:
        # KRX OpenAPI 이용신청 여부 사전 체크
        if KRX_KEY:
            try:
                probe = await client.get(
                    f"{KRX_BASE}/stk_bydd_trd",
                    params={"basDd": TODAY},
                    headers={"AUTH_KEY": KRX_KEY},
                    timeout=8,
                )
                if probe.status_code == 200:
                    print("  KRX OpenAPI 활성 — 주가 KRX 우선 수집", flush=True)
                else:
                    body_txt = probe.text[:80] if probe.text else ""
                    print(f"  KRX OpenAPI 미활성 (HTTP {probe.status_code}: {body_txt}) — yfinance로 대체", flush=True)
                    if probe.status_code == 401:
                        print("  ※ openapi.krx.co.kr → 마이페이지 → API 이용신청 필요", flush=True)
            except Exception as e:
                print(f"  KRX OpenAPI 연결 실패: {e} — yfinance로 대체", flush=True)
        print("[3/3] 수집 시작...", flush=True)
        tasks = [process_one(client, cc, sc, cls) for cc, sc, cls in entries]
        for i in range(0, len(tasks), 50):
            await asyncio.gather(*tasks[i: i + 50])
            print(f"  진행: {min(i+50, len(entries))}/{len(entries)}", flush=True)

    print("완료", flush=True)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
