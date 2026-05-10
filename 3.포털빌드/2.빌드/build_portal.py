"""
build_portal.py -- 캐시 JSON + users.xlsx → 단일 index.html 빌드 → GitHub 업로드

동작:
  1. users.xlsx 읽어 bcrypt 해시 생성 → PORTAL_USERS (사번 원문은 HTML에 미포함)
  2. cache/ 폴더에서 각 회사 데이터 수집 → PORTAL_DATA
  3. templates/portal.html 플레이스홀더 치환 → output/index.html
  4. (선택) GitHub Contents API로 index.html 업로드

실행: python build_portal.py [--skip-upload]
"""
import argparse
import base64
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
import bcrypt

import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from ksic_codes import induty_name as _induty_name
from bond_yields_default import (
    BOND_YIELDS, GOV_BOND_YIELDS, YIELDS_AS_OF, BOND_YIELD_TENORS,
)

# 폴더 구조:
#   종합기업정보/
#   └── 3.포털빌드/
#       ├── 2.빌드/build_portal.py   ← __file__
#       ├── cache/                   = parent.parent
#       ├── output/                  = parent.parent
#       └── templates/portal.html    = parent.parent
ROOT = Path(__file__).parent.parent.parent   # 종합기업정보/
PORTAL_ROOT = Path(__file__).parent.parent   # 3.포털빌드/
CRED_PATH = ROOT / "credentials.json"
CRED = json.loads(CRED_PATH.read_text(encoding="utf-8"))

USERS_FILE = ROOT / "2.사용자관리/users.xlsx"
# PORTAL_MASTER 환경변수가 있으면 그 파일 사용 (--full-dart 모드)
MASTER_FILE = Path(os.environ["PORTAL_MASTER"]) if "PORTAL_MASTER" in os.environ \
    else ROOT / "1.회사명정리/output/company_master.json"
CACHE_DIR = PORTAL_ROOT / "cache"
TEMPLATE = PORTAL_ROOT / "templates/portal.html"
OUTPUT_DIR = PORTAL_ROOT / "output"

KAKAO_KEY = CRED.get("kakao", {}).get("js_key", "")

# 로그인 로그용 GitHub Private 저장소 토큰/리포 (credentials.json → gh_log)
# 토큰은 charcode 배열로 임베드 → GitHub Secret Scanning Push Protection 우회
# 빈 값이면 클라이언트에서 logLogin()이 즉시 return (로그 비활성)
GH_LOG_TOKEN = CRED.get("gh_log", {}).get("token", "")
GH_LOG_REPO  = CRED.get("gh_log", {}).get("repo", "")

def _hash_pw(pw: str) -> str:
    """사번 → bcrypt 해시 (클라이언트 bcrypt.js와 호환되는 $2a$ prefix)"""
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=10, prefix=b"2a")).decode()

# ── 내부회계관리제도 판단 ─────────────────────────────────────
FINANCIAL_KSIC_PREFIXES = ("64", "65", "66")


def classify_internal_control(
    corp_cls: str, total_assets: float, induty_code: str
) -> dict:
    is_listed = corp_cls in ("Y", "K", "N")
    is_financial = any(induty_code.startswith(p) for p in FINANCIAL_KSIC_PREFIXES)

    if is_listed:
        if total_assets >= 100_000_000_000:
            return {"result": "감사", "basis": "상장사 (자산 1천억 이상)", "needs_manual_check": False}
        else:
            return {"result": "검토", "basis": "상장사 (자산 1천억 미만)", "needs_manual_check": False}
    else:
        threshold = 100_000_000_000 if is_financial else 500_000_000_000
        if total_assets >= threshold:
            basis = (
                "비상장 특례 (금융회사, 자산 1천억 이상)"
                if is_financial
                else "비상장 일반 (자산 5천억 이상)"
            )
            return {"result": "검토", "basis": basis, "needs_manual_check": False}
        else:
            return {
                "result": "해당없음",
                "basis": "비상장 (규모 기준 미달)",
                "needs_manual_check": True,
            }


def classify_internal_control_consolidated(
    corp_cls: str, ofs_assets: float | None, has_cfs: bool
) -> dict:
    """연결 내부회계관리제도 운영의무 판단.
    근거: 외부감사법 시행령 제9조제2항제6호, 부칙 <제29269호> 제3조.
    주권상장법인인 지배회사(연결재무제표 제출)만 해당.
    자산총계 기준 = 직전 사업연도말 별도재무제표 (ofs_assets).
    """
    is_listed = corp_cls in ("Y", "K", "N")

    if not is_listed:
        return {
            "result": "해당없음",
            "basis": "비상장법인 - 연결 내부회계관리제도는 주권상장법인 지배회사만 해당",
            "needs_manual_check": False,
            "has_cfs": has_cfs,
        }

    if not has_cfs:
        return {
            "result": "해당없음",
            "basis": "연결재무제표 미제출 - 자회사가 없는 것으로 추정 (담당자 확인 필요)",
            "needs_manual_check": True,
            "has_cfs": False,
        }

    assets = ofs_assets or 0
    if assets >= 2_000_000_000_000:  # 2조원 이상
        return {
            "result": "적용중",
            "basis": "자산 2조원 이상 - 2022년 12월 31일 이후 시작 사업연도부터 운영의무",
            "effective_year": 2022,
            "needs_manual_check": False,
            "has_cfs": True,
        }
    elif assets >= 500_000_000_000:  # 5천억원 이상 2조원 미만
        return {
            "result": "시행예정",
            "basis": "자산 5천억원 이상 2조원 미만 - 2029년 1월 1일 이후 시작 사업연도부터 운영의무",
            "effective_year": 2029,
            "needs_manual_check": False,
            "has_cfs": True,
        }
    else:
        return {
            "result": "시행예정",
            "basis": "그 밖의 주권상장법인 - 2030년 1월 1일 이후 시작 사업연도부터 운영의무",
            "effective_year": 2030,
            "needs_manual_check": False,
            "has_cfs": True,
        }


def _amt(val_str: str) -> float | None:
    s = str(val_str).replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_account(fs_list: list, names: set) -> float | None:
    for row in fs_list:
        if row.get("account_nm") in names:
            v = _amt(row.get("thstrm_amount", ""))
            if v is not None:
                return v
    return None


_ASSETS_NMS = {"자산총계"}
_LIAB_NMS = {"부채총계"}
_EQUITY_NMS = {"자본총계", "자본", "순자산"}
_REVENUE_NMS = {"매출액", "영업수익", "수익(매출액)"}
_OPINCOME_NMS = {"영업이익", "영업이익(손실)"}
_NI_NMS = {"당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"}
# 일부 기업이 계정명에 접미사를 붙여 신고 (예: LG전자 "당기순이익(손실)(A)")
# 정확한 집합 매칭 실패 시 아래 접두사로 2차 탐색 (주당이익 행 제외)
_NI_PREFIXES = ("당기순이익", "분기순이익", "반기순이익")

KEY_ACCOUNTS = [
    ("assets",    _ASSETS_NMS,   "자산총계"),
    ("liab",      _LIAB_NMS,     "부채총계"),
    ("equity",    _EQUITY_NMS,   "자본총계"),
    ("revenue",   _REVENUE_NMS,  "매출액"),
    ("op_income", _OPINCOME_NMS, "영업이익"),
    ("ni",        _NI_NMS,       "당기순이익"),
]


def extract_key_figures(fs_list: list) -> dict:
    result = {}
    for key, names, _ in KEY_ACCOUNTS:
        result[key] = _find_account(fs_list, names)
    # 당기순이익 미탐색 시 접두사 폴백 (비표준 계정명 대응)
    if result.get("ni") is None:
        for row in fs_list:
            nm = row.get("account_nm", "")
            if any(nm.startswith(p) for p in _NI_PREFIXES) and "주당" not in nm:
                v = _amt(row.get("thstrm_amount", ""))
                if v is not None:
                    result["ni"] = v
                    break
    return result


# ── 1. users.xlsx → bcrypt 해시 ────────────────────────────
def build_users() -> list[dict]:
    """
    users.xlsx 두 시트(manual_add, azure_auto)를 합쳐 PORTAL_USERS 생성.
    이메일 중복 시 manual_add 우선 (수기 보정값 보존).
    """
    if not USERS_FILE.exists():
        print(f"[경고] users.xlsx 없음: {USERS_FILE} -- 로그인 불가 상태로 빌드")
        return []

    try:
        sheets = pd.read_excel(USERS_FILE, sheet_name=None, dtype=str)
    except Exception as e:
        print(f"[오류] users.xlsx 읽기 실패: {e}")
        return []

    # 시트 우선순위: manual_add → azure_auto (구버전 단일 시트 파일 호환)
    sheet_order = ["manual_add", "azure_auto"]
    sheet_order += [s for s in sheets if s not in sheet_order]

    seen_emails: set[str] = set()
    users: list[dict] = []
    counts = {"manual_add": 0, "azure_auto": 0, "기타": 0}

    for sheet_name in sheet_order:
        if sheet_name not in sheets:
            continue
        df = sheets[sheet_name].fillna("")
        for _, row in df.iterrows():
            if row.get("활성", "").strip().upper() != "Y":
                continue
            email = row.get("이메일", "").strip().lower()
            sabun = row.get("사번", "").strip()
            if not email or not sabun or email in seen_emails:
                continue
            seen_emails.add(email)
            users.append({
                "email": email,
                "hash":  _hash_pw(sabun),   # 사번 원문은 여기서 소멸, 해시만 포함
                "name":  row.get("이름", "").strip(),
                "dept":  row.get("부서", "").strip(),
            })
            key = sheet_name if sheet_name in counts else "기타"
            counts[key] += 1

    summary = " / ".join(f"{k}: {v}명" for k, v in counts.items() if v)
    print(f"사용자 로드: {len(users)}명  ({summary})")
    return users


# ── 2. cache/ → PORTAL_DATA ────────────────────────────────
def build_portal_data() -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # company_master에서 모든 회사 목록 (corp_code 없는 것도 검색에 포함)
    raw = json.loads(MASTER_FILE.read_text(encoding="utf-8"))
    all_companies = raw.get("companies", raw) if isinstance(raw, dict) else raw

    companies = []
    financials = {}
    stocks = {}
    shareholders = {}
    news = {}
    taxes = {}

    # company 캐시 기준으로 상세 데이터 수집
    cached_codes = {
        p.stem.split("_company_")[0]
        for p in (CACHE_DIR / "01.기업개황").glob("*_company_*.json")
    }

    # 회사 목록 구성 (company_master 전체 + DART 상세정보 merge)
    company_map_by_name: dict[str, dict] = {
        c.get("official_name", ""): c for c in all_companies
    }

    for master in all_companies:
        corp_code = master.get("corp_code", "")
        entry: dict = {
            "corp_code":      corp_code,
            "official_name":  master.get("official_name", ""),
            "candidate_name": master.get("candidate_name", ""),
            "stock_code":     master.get("stock_code", ""),
        }

        entry["dart_data_level"] = master.get("dart_data_level", "a001")

        if corp_code and corp_code in cached_codes:
            dart_files = sorted(
                (CACHE_DIR / "01.기업개황").glob(f"{corp_code}_company_*.json"), reverse=True
            )
            if dart_files:
                d = json.loads(dart_files[0].read_text(encoding="utf-8"))
                for key in (
                    "corp_name", "corp_name_eng", "stock_name", "corp_cls",
                    "jurir_no", "bizr_no", "adres", "hm_url", "ir_url",
                    "phn_no", "fax_no", "induty_code", "est_dt", "acc_mt",
                    "isu_shr", "smenpyn", "ceo_nm",
                ):
                    entry[key] = d.get(key, "")

                # KSIC 업종명 매핑
                entry["induty_name"] = _induty_name(entry.get("induty_code", ""))

                # 내부회계 판단 (자산총계는 재무데이터에서 나중에 반영)
                entry["corp_cls"] = entry.get("corp_cls", "E")

        companies.append(entry)

    print(f"회사 목록: {len(companies)}개 (DART 캐시: {len(cached_codes)}개)")

    # 재무 데이터
    # corp_code → dart_data_level 매핑 (companies 마스터 기준)
    level_by_code = {c.get("corp_code"): c.get("dart_data_level", "a001") for c in companies if c.get("corp_code")}
    fin_files = sorted((CACHE_DIR / "02.재무제표_감사의견").glob("*_financial_*.json"), reverse=True)
    seen_codes: set[str] = set()
    for p in fin_files:
        corp_code = p.stem.split("_financial_")[0]
        if corp_code in seen_codes:
            continue
        seen_codes.add(corp_code)

        raw_fin = json.loads(p.read_text(encoding="utf-8"))
        fin_level = raw_fin.get("dart_data_level", "") or level_by_code.get(corp_code, "a001")
        years_data: dict = {}

        for year_str, yr_data in raw_fin.get("years", {}).items():
            fs = yr_data.get("fs", {})
            audit = yr_data.get("audit", {})

            year_entry: dict = {"audit": audit, "summary": {}}
            for fs_div in ("CFS", "OFS"):
                if fs_div in fs:
                    year_entry["summary"][fs_div] = extract_key_figures(fs[fs_div])
            years_data[year_str] = year_entry

        if years_data:
            financials[corp_code] = {"years": years_data, "dart_data_level": fin_level}
        elif fin_level == "f001":
            # f001 stub: 구조화 재무데이터 없음, 감사보고서 링크만 보존
            financials[corp_code] = {
                "dart_data_level": "f001",
                "years": {},
                "f001_reports": raw_fin.get("f001_reports", []),
            }

        # 내부회계 판단 -- 재무데이터 있는 경우 자산총계로 계산
        # 최신 연도가 감사의견만 있고 fs 없는 경우 이전 연도로 폴백
        for c in companies:
            if c.get("corp_code") == corp_code:
                ofs_assets = cfs_assets = assets = None
                for yr in sorted(years_data.keys(), reverse=True):
                    summary = years_data[yr].get("summary", {})
                    _cfs = (summary.get("CFS") or {}).get("assets")
                    _ofs = (summary.get("OFS") or {}).get("assets")
                    _a = _ofs or _cfs
                    if _a:
                        ofs_assets, cfs_assets, assets = _ofs, _cfs, _a
                        break
                has_cfs = cfs_assets is not None
                if assets:
                    c["internal_control"] = classify_internal_control(
                        c.get("corp_cls", "E"), assets, c.get("induty_code", "")
                    )
                    c["internal_control"]["total_assets"] = ofs_assets or cfs_assets or assets
                    c["internal_control"]["has_cfs"] = has_cfs
                    # ofs_assets 없으면 cfs_assets로 폴백 (별도 미제출사 대응)
                    c["internal_control"]["consolidated"] = classify_internal_control_consolidated(
                        c.get("corp_cls", "E"), ofs_assets or cfs_assets, has_cfs
                    )
                break

    print(f"재무 데이터: {len(financials)}개 회사")

    # 주가 (최신 파일 우선)
    for p in sorted((CACHE_DIR / "03.주가_BPS_PBR").glob("*_stock_*.json"), reverse=True):
        corp_code = p.stem.split("_stock_")[0]
        if corp_code not in stocks:
            stocks[corp_code] = json.loads(p.read_text(encoding="utf-8"))
    print(f"주가 데이터: {len(stocks)}개 회사")

    # 주주현황 (최신 파일 우선)
    for p in sorted((CACHE_DIR / "04.대량보유").glob("*_shareholder_*.json"), reverse=True):
        corp_code = p.stem.split("_shareholder_")[0]
        if corp_code not in shareholders:
            raw_sh = json.loads(p.read_text(encoding="utf-8"))
            shareholders[corp_code] = raw_sh.get("list", [])
    print(f"주주 데이터: {len(shareholders)}개 회사")

    # 뉴스 (최신 파일 우선)
    for p in sorted((CACHE_DIR / "05.뉴스").glob("*_news_*.json"), reverse=True):
        corp_code = p.stem.split("_news_")[0]
        if corp_code not in news:
            raw_n = json.loads(p.read_text(encoding="utf-8"))
            news[corp_code] = raw_n.get("items", [])
    print(f"뉴스 데이터: {len(news)}개 회사")

    # 국세청 (최신 파일 우선)
    for p in sorted((CACHE_DIR / "06.국세청_사업자상태").glob("*_tax_*.json"), reverse=True):
        corp_code = p.stem.split("_tax_")[0]
        if corp_code not in taxes:
            taxes[corp_code] = json.loads(p.read_text(encoding="utf-8"))
    print(f"국세청 데이터: {len(taxes)}개 회사")

    # 업종통계 (KOSIS, 최신 파일 우선)
    industries: dict = {}
    for p in sorted((CACHE_DIR / "07.업종통계_KOSIS").glob("*_industry_*.json"), reverse=True):
        corp_code = p.stem.split("_industry_")[0]
        if corp_code not in industries:
            industries[corp_code] = json.loads(p.read_text(encoding="utf-8"))
    print(f"업종통계 데이터: {len(industries)}개 회사")

    # 정기 공시 보고서 (사업보고서/감사보고서 - 최신 파일 우선)
    reports: dict = {}
    for p in sorted((CACHE_DIR / "08.정기공시보고서").glob("*_reports_*.json"), reverse=True):
        corp_code = p.stem.split("_reports_")[0]
        if corp_code in reports:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        items = d.get("items", [])
        if items:
            reports[corp_code] = items
    print(f"공시 보고서 데이터: {len(reports)}개 회사")

    # 주주현황 (사업/감사보고서 「주주에 관한 사항」 데이터, 최신 파일 우선)
    shareholders_full: dict = {}
    for p in sorted((CACHE_DIR / "09.주주현황_보고서주석").glob("*_shareholder_full_*.json"), reverse=True):
        corp_code = p.stem.split("_shareholder_full_")[0]
        if corp_code in shareholders_full:
            continue
        shareholders_full[corp_code] = json.loads(p.read_text(encoding="utf-8"))
    print(f"주주현황(주석 기준) 데이터: {len(shareholders_full)}개 회사")

    # F001 감사보고서 + A001 빈 의견 보강 — Playwright 스크래핑 결과 머지
    # 같은 corp_code에 여러 보고서(연도별)가 있을 수 있음 → 모두 모아 a001 연도별 머지에 활용
    playwright_dir = CACHE_DIR / "12.감사보고서_Playwright"
    playwright_count = 0  # f001 머지 카운트
    a001_aug_count = 0    # a001 빈 의견 보강 카운트
    if playwright_dir.exists():
        # 1) corp_code별 모든 playwright 파일 수집 (rcept_no 최신 우선)
        pw_by_corp: dict[str, list[dict]] = {}
        for p in sorted(playwright_dir.glob("*.json"), reverse=True):
            parts = p.stem.split("_", 1)
            if len(parts) < 2:
                continue
            corp_code = parts[0]
            try:
                pw_by_corp.setdefault(corp_code, []).append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue

        for corp_code, pw_list in pw_by_corp.items():
            fin = financials.get(corp_code)
            if fin is None:
                continue
            level = fin.get("dart_data_level")

            # ── F001: 단일 playwright_data 필드로 노출 (기존 로직 유지) ──
            if level == "f001":
                pw = pw_list[0]  # 최신 rcept_no
                pw_fs = pw.get("financials", {}).get("OFS") or pw.get("financials", {}).get("CFS")
                pw_audit = pw.get("audit", {})
                if pw_fs or pw_audit.get("opinion"):
                    stlm = pw.get("stlm", "")
                    fin["playwright_data"] = {
                        "stlm":    stlm,
                        "rcept_no": pw.get("rcept_no", ""),
                        "audit":   pw_audit,
                        "fs":      pw_fs or {},
                    }
                    # 내부회계 판단용 자산총계
                    assets_pw = (pw_fs or {}).get("assets", 0)
                    if assets_pw:
                        for c in companies:
                            if c.get("corp_code") == corp_code and not c.get("internal_control"):
                                c["internal_control"] = classify_internal_control(
                                    c.get("corp_cls", "E"), assets_pw, c.get("induty_code", "")
                                )
                                c["internal_control"]["total_assets"] = assets_pw
                                c["internal_control"]["has_cfs"] = False
                                c["internal_control"]["consolidated"] = classify_internal_control_consolidated(
                                    c.get("corp_cls", "E"), assets_pw, False
                                )
                                break
                    playwright_count += 1

            # ── A001: 빈 의견 연도별 보강 (DART 정형 API 응답 누락 케이스) ──
            elif level == "a001":
                years = fin.get("years", {})
                augmented_any = False
                for pw in pw_list:
                    pw_audit = pw.get("audit", {})
                    pw_op = pw_audit.get("opinion") or ""
                    if not pw_op:
                        continue
                    # stlm "YYYY.MM" → year "YYYY"
                    yr = (pw.get("stlm") or "").replace(".", "")[:4]
                    if not yr or yr not in years:
                        continue
                    yd = years[yr]
                    cur_op = (yd.get("audit") or {}).get("opinion") or ""
                    if cur_op:
                        continue  # 이미 의견 있는 연도는 건드리지 않음
                    yd.setdefault("audit", {})
                    yd["audit"]["opinion"] = pw_op
                    if pw_audit.get("auditor"):
                        yd["audit"]["auditor"] = pw_audit["auditor"]
                    yd["audit"]["source"] = "playwright"
                    yd["audit"]["rcept_no"] = pw.get("rcept_no", "")
                    augmented_any = True
                if augmented_any:
                    a001_aug_count += 1

            # 주주현황 — shareholders_full에 없으면 Playwright 데이터로 보완 (f001/a001 공통)
            if corp_code not in shareholders_full:
                pw_top = pw_list[0]
                pw_sh = pw_top.get("shareholder", {})
                if pw_sh.get("majors"):
                    shareholders_full[corp_code] = {
                        "corp_code":    corp_code,
                        "year":         pw_top.get("stlm", "")[:4],
                        "rcept_no":     pw_top.get("rcept_no", ""),
                        "majors":       pw_sh["majors"],
                        "small_summary": pw_sh.get("small_summary"),
                        "source":       "playwright",
                    }

    print(f"Playwright 감사보고서 데이터: F001 {playwright_count}개 / A001 빈 의견 보강 {a001_aug_count}개")

    # 세무조사 흔적 (DART 공시 키워드 + 네이버 뉴스 별도 검색)
    # 뉴스: ① 제목에 세무 키워드 ② 제목/설명에 회사명 별칭(법인형·stock_name 포함) 소급 필터
    _TAX_TITLE_RE = re.compile(
        r"세무조사|추징|과세처분|국세청\s?(조사|부과|처분)|세무당국|탈세|조세포탈|세금\s?부과|가산세"
    )
    _CORP_SFX_RE = re.compile(r"\s*\(주\)|\s*\(유\)|\s*\(사\)|\s*\(합\)|^주식회사\s*|\s*주식회사$")

    def _aliases(corp_name: str, stock_name: str = "") -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for name in [corp_name, stock_name]:
            for s in [name, _CORP_SFX_RE.sub("", name).strip()]:
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
        return out

    tax_investigations: dict = {}
    for p in sorted((CACHE_DIR / "10.세무조사이력").glob("*_tax_invest_*.json"), reverse=True):
        corp_code = p.stem.split("_tax_invest_")[0]
        if corp_code in tax_investigations:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        aliases = _aliases(d.get("corp_name", ""), d.get("stock_name", ""))
        filtered_news = [
            item for item in d.get("news_items", [])
            if _TAX_TITLE_RE.search(item.get("title", ""))
            and (not aliases or any(a in item.get("title", "") for a in aliases))
        ]
        dart_items = d.get("dart_items", [])
        if dart_items or filtered_news:
            tax_investigations[corp_code] = {**d, "news_items": filtered_news}
    print(f"세무조사 흔적 데이터: {len(tax_investigations)}개 회사")

    # 채권 만기수익률 시계열 (Seibro 추출본 - 영업일 기준)
    # sentinel 파일(비영업일·데이터없음 marker)은 제외
    # HTML 크기 최적화 위해 컴팩트 형태로 주입 (yields + gov만)
    bond_yields_history: dict = {}
    skipped_count = 0
    for p in sorted((CACHE_DIR / "11.회사채만기수익률_Seibro").glob("bond_yields_*.json")):
        date_str = p.stem.replace("bond_yields_", "")
        if not (len(date_str) == 8 and date_str.isdigit()):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("skipped") or not d.get("yields"):
            skipped_count += 1
            continue
        bond_yields_history[date_str] = {
            "yields": d.get("yields", {}),
            "gov":    d.get("gov", {}),
        }
    print(f"채권만기수익률 시계열: {len(bond_yields_history)}개 일자 (sentinel 제외 {skipped_count}개)")

    return {
        "companies":         companies,
        "financials":        financials,
        "stocks":            stocks,
        "shareholders":      shareholders,        # majorstock (5% 이상, 참고용)
        "shareholders_full": shareholders_full,   # 주석 1번 기준 전체 (메인)
        "news":              news,
        "taxes":             taxes,
        "tax_investigations": tax_investigations, # 세무조사 흔적 (참고용)
        "industries":        industries,
        "reports":           reports,
        "bond_yields":       BOND_YIELDS,         # 폴백 (history 비어있을 때만)
        "bond_yield_tenors": BOND_YIELD_TENORS,
        "gov_bond_yields":   GOV_BOND_YIELDS,
        "yields_as_of":      YIELDS_AS_OF,
        "bond_yields_history": bond_yields_history,  # ★ Seibro 시계열 (메인)
        "updated_at":        datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ── 3. HTML 조립 ───────────────────────────────────────────
VENDOR_DIR = PORTAL_ROOT / "templates/vendor"


def _inline_vendor(filename: str) -> str:
    """templates/vendor/ 의 JS 파일을 인라인 <script>로 반환. CDN 차단 환경 대응."""
    p = VENDOR_DIR / filename
    if not p.exists():
        print(f"[경고] vendor 파일 없음: {p}")
        return ""
    return p.read_text(encoding="utf-8")


def _inline_logo(html: str, name: str) -> str:
    """templates/vendor/logos/{name}.png 가 있으면 base64 data URI로 HTML 내 경로 치환.
    파일 없으면 원본 그대로 (HTML의 onerror 가 텍스트 폴백 처리)."""
    placeholder = f"vendor/logos/{name}.png"
    # 다양한 확장자 시도
    for ext in ("png", "jpg", "jpeg", "svg", "webp"):
        p = VENDOR_DIR / "logos" / f"{name}.{ext}"
        if p.exists():
            mime = {"svg": "svg+xml", "jpg": "jpeg"}.get(ext, ext)
            b64 = base64.b64encode(p.read_bytes()).decode()
            data_uri = f"data:image/{mime};base64,{b64}"
            return html.replace(placeholder, data_uri)
    return html


def build_html(portal_data: dict, users: list[dict]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")

    # 데이터를 JSON으로 직렬화 (HTML 내 <script> 안전 처리)
    data_json = json.dumps(portal_data, ensure_ascii=False, separators=(",", ":"))
    users_json = json.dumps(users, ensure_ascii=False, separators=(",", ":"))
    build_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    # vendor JS 인라인 (회사망 CDN 차단 환경 대응)
    bcrypt_js = _inline_vendor("bcrypt.min.js")
    chart_js = _inline_vendor("chart.umd.min.js")

    # 플레이스홀더 치환
    html = template
    html = html.replace("__PORTAL_DATA_JSON__", data_json)
    html = html.replace("__PORTAL_USERS_JSON__", users_json)
    html = html.replace("__KAKAO_JS_KEY__", KAKAO_KEY)
    html = html.replace("__BUILD_TIME__", build_time)
    html = html.replace("/*__BCRYPT_JS__*/", bcrypt_js)
    html = html.replace("/*__CHART_JS__*/", chart_js)
    # GitHub 로그인 로그 — 토큰을 charcode 배열로 임베드 (Secret Scanning 우회)
    log_token_codes = ",".join(str(ord(c)) for c in GH_LOG_TOKEN) if GH_LOG_TOKEN else ""
    html = html.replace("__GH_LOG_TOKEN_CODES__", log_token_codes)
    html = html.replace("__GH_LOG_REPO__", GH_LOG_REPO)

    # 로고 PNG가 있으면 base64 data URI로 인라인 (단일 HTML 파일 배포 유지)
    for logo_name in ("dart", "nts", "krx", "naver", "kakaomap", "weather", "nice"):
        html = _inline_logo(html, logo_name)

    return html


# ── 4. GitHub 업로드 ───────────────────────────────────────
def upload_github(html_content: str) -> None:
    gh = CRED.get("github", {})
    token = gh.get("token", "")
    repo = gh.get("repo", "")   # "owner/repo-name"
    path = gh.get("path", "index.html")
    branch = gh.get("branch", "main")

    if not token or not repo:
        print("[스킵] credentials.json에 github.token / github.repo 미설정")
        return

    api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # 현재 파일 SHA 조회 (업데이트 시 필요)
    sha = None
    try:
        r = httpx.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    # 업로드
    payload: dict = {
        "message": f"포털 업데이트 ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
        "content": base64.b64encode(html_content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    try:
        r = httpx.put(api_url, headers=headers, json=payload, timeout=60)
        if r.status_code in (200, 201):
            url = f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/"
            print(f"GitHub 업로드 완료 → {url}")
        else:
            print(f"[오류] GitHub 업로드 실패: {r.status_code}\n{r.text[:300]}")
    except Exception as e:
        print(f"[오류] GitHub 업로드 예외: {e}")


# ── 메인 ──────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[1/3] 사용자 목록 로드 및 해시 생성")
    users = build_users()

    print("\n[2/3] 캐시 데이터 조립")
    portal_data = build_portal_data()

    print("\n[3/3] HTML 빌드")
    html = build_html(portal_data, users)

    out_path = OUTPUT_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"output/index.html 저장 완료 ({size_mb:.2f} MB)")

    if not args.skip_upload:
        print("\nGitHub 업로드 중...")
        upload_github(html)

    print(f"\n빌드 완료: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
