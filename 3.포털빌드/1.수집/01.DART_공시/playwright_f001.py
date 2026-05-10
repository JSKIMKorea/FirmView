"""
playwright_f001.py -- F001 감사보고서에서 재무제표 + 주주현황 + 감사의견 추출

DART 감사보고서 HTML 문서를 Playwright로 접근하여 파싱합니다.

누적 캐시 방식:
  cache/12.감사보고서_Playwright/{corp_code}_{rcept_no}.json
  - 동일 rcept_no 재실행 시 스킵 (보고서 내용 불변)
  - PDF 보고서는 스킵 (pass)

다른 컴퓨터에서 실행 후 cache/12.감사보고서_Playwright/ 폴더만 복사하면 됩니다.

사전 준비:
  pip install playwright httpx
  playwright install chromium

실행:
  python playwright_f001.py              # 전체 F001 기업
  python playwright_f001.py 50          # 최대 50개사
  python playwright_f001.py --install   # Playwright 브라우저 설치
"""
import asyncio
import json
import os
import random
import re
import sys
from datetime import date
from pathlib import Path

# Windows 콘솔 UTF-8 출력 (line_buffering=True → 즉시 출력)
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
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).parent.parent.parent.parent
CRED = json.loads((ROOT / "credentials.json").read_text(encoding="utf-8"))
CACHE_ROOT = Path(__file__).parent.parent.parent / "cache"
PERM_DIR          = CACHE_ROOT / "12.감사보고서_Playwright"
REPORTS_CACHE_DIR = CACHE_ROOT / "08.정기공시보고서"
FIN_PERM_DIR      = CACHE_ROOT / "02.재무제표_감사의견" / "permanent"
FULL_MASTER = CACHE_ROOT / "00.회사목록" / "company_master_full.json"
TODAY = date.today().strftime("%Y%m%d")

DART_BASE = "https://dart.fss.or.kr"
SEM = asyncio.Semaphore(1)   # 동시 페이지 1개 (DART IP 차단 방지)


# ── HTML 파싱 유틸 ──────────────────────────────────────────
def _strip_tags(html: str) -> str:
    text = re.sub(r'<[^>]+>', '', html)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return re.sub(r'\s+', ' ', text).strip()


def _parse_tables(html: str) -> list[list[list[str]]]:
    """HTML에서 테이블 구조 추출 (중첩 제외, 단순 파싱)"""
    tables = []
    # 중첩 방지: 가장 바깥 <table> 태그만 매칭
    depth = 0
    start = -1
    segments = []
    for m in re.finditer(r'<(/?)table[^>]*>', html, re.IGNORECASE):
        if not m.group(1):   # 열기 태그
            if depth == 0:
                start = m.start()
            depth += 1
        else:                # 닫기 태그
            depth -= 1
            if depth == 0 and start >= 0:
                segments.append(html[start: m.end()])
                start = -1

    for seg in segments:
        rows = []
        for rm in re.finditer(r'<tr[^>]*>(.*?)</tr>', seg, re.DOTALL | re.IGNORECASE):
            cells = []
            for cm in re.finditer(r'<t[dh][^>]*>(.*?)</t[dh]>', rm.group(1), re.DOTALL | re.IGNORECASE):
                cells.append(_strip_tags(cm.group(1)))
            if any(c for c in cells):
                rows.append(cells)
        if len(rows) >= 2:
            tables.append(rows)
    return tables


def _clean_num(text: str) -> int | None:
    """숫자 문자열 파싱 (괄호=음수, 콤마 제거)"""
    s = text.strip().replace(',', '').replace(' ', '').replace('\xa0', '')
    if not s or s in ('-', '―', '–', '', 'N/A', '-'):
        return None
    neg = s.startswith('(') and s.endswith(')')
    s = s.strip('()')
    try:
        v = int(float(s))
        return -v if neg else v
    except (ValueError, TypeError):
        return None


def _detect_unit(html: str) -> int:
    """단위 감지: 원(1) / 천원(1000) / 백만원(1000000)"""
    m = re.search(r'단위\s*[:：]\s*(원|천\s*원|백만\s*원|천원|백만원)', html)
    if m:
        u = m.group(1).replace(' ', '')
        if '백만' in u:
            return 1_000_000
        if '천' in u:
            return 1_000
    return 1


# 핵심 계정명 (감사보고서별 표기 차이 대응)
_FS_KEYS: dict[str, set[str]] = {
    'assets':    {'자산총계', '자 산 총 계', '자산합계', '자산 총계', '총자산'},
    'liab':      {'부채총계', '부 채 총 계', '부채합계', '부채 총계', '총부채'},
    'equity':    {'자본총계', '자 본 총 계', '자본합계', '자본 총계', '순자산', '총자본'},
    'revenue':   {'매출액', '매 출 액', '영업수익', '수익(매출액)', '영업수익(매출액)', '총수익', '총 수 익'},
    'op_income': {'영업이익', '영업이익(손실)', '영업손익', '영업손실'},
    'ni':        {'당기순이익', '당기순이익(손실)', '당기순손익', '당기순손실', '분기순이익', '반기순이익'},
}


_NOTE_REF_PAT = re.compile(r'^\d{1,2}(?:,\d{1,2})*$')  # 1-2자리 그룹만 (재무 숫자는 3자리 그룹)

def _is_note_ref(cell: str) -> bool:
    """주석 참조번호 패턴 (예: '9', '35', '9,35', '4,6,8,35') — 재무금액과 구별"""
    return bool(_NOTE_REF_PAT.match(cell.strip()))


def _extract_fs_from_tables(tables: list, unit: int) -> dict:
    """테이블 목록에서 핵심 재무 계정 추출"""
    result: dict[str, int] = {}
    for rows in tables:
        for row in rows:
            if not row:
                continue
            label = row[0].strip()
            for key, names in _FS_KEYS.items():
                if key in result:
                    continue
                if any(n == label or n in label for n in names):
                    # 당기 값: 보통 2~5번째 열 (주석 참조번호 열 건너뜀)
                    for cell in row[1:6]:
                        if _is_note_ref(cell.strip()):
                            continue
                        v = _clean_num(cell)
                        if v is not None:
                            result[key] = v * unit
                            break
    return result


def _extract_shareholder_from_tables(tables: list) -> dict:
    """테이블 목록에서 주주현황 파싱 (주석 1번 기준)

    - 2행 헤더 지원 (예: '주 주 명 / 당기말 / 전기말' + '주식수 / 지분율(%)')
    - 헤더 셀 내 공백 정규화 후 매칭
    """
    majors = []
    SH_KEYS   = {'주주명', '주주', '성명', '이름', '소유자'}
    RATIO_KEYS = {'지분율', '비율', '소유비율', '지분비율'}
    QTY_KEYS   = {'주식수', '소유주식수', '보유주식수', '주식'}
    SKIP_NAMES = {'합계', '계', '소계', '합 계', '소 계', '-', ''}

    for rows in tables:
        if len(rows) < 2:
            continue

        # 상위 2행을 합쳐 헤더 키워드 확인 (공백 제거)
        header_all = ' '.join(' '.join(r) for r in rows[:2]).replace(' ', '')
        has_sh = any(k in header_all for k in SH_KEYS)
        has_rt = any(k in header_all for k in RATIO_KEYS)
        if not (has_sh and has_rt):
            continue

        # 실제 데이터 시작 행 결정 (두 번째 행도 서브헤더면 skip)
        data_start = 1
        if len(rows) >= 2:
            second = rows[1]
            second_clean = ' '.join(second).replace(' ', '')
            # 두 번째 행이 숫자보다 키워드 위주면 서브헤더
            if any(k in second_clean for k in QTY_KEYS | RATIO_KEYS | {'당기말', '전기말', '기말'}):
                data_start = 2

        # 헤더 행들 통합해서 열 인덱스 파악 (heuristic)
        # 첫 번째 행: 이름 열 찾기
        nm_col = 0  # 이름은 항상 첫 번째 열
        ratio_col = qty_col = rel_col = -1

        # 두 번째 헤더 행에서 지분율/주식수 찾기 (실제 데이터 열 기준으로 offset)
        # 데이터 행 패턴으로 열 추론: 이름(0), 주식수(1+), 지분율(2+)
        # 실제 데이터 행에서 추론
        for row in rows[data_start:]:
            nm = row[0].strip().replace(' ', '') if row else ''
            if nm in SKIP_NAMES:
                continue
            # 각 열의 값을 보고 지분율과 주식수 추론
            for ci in range(1, min(len(row), 6)):
                cell = row[ci].strip().replace(',', '').replace('%', '')
                try:
                    v = float(cell)
                except (ValueError, TypeError):
                    continue
                if ratio_col == -1 and 0 < v <= 100 and ('.' in row[ci] or v < 10):
                    ratio_col = ci
                elif qty_col == -1 and v > 100:
                    qty_col = ci
            if ratio_col != -1:
                break

        if ratio_col == -1:
            continue

        # 데이터 추출
        for row in rows[data_start:]:
            nm = row[0].strip() if row else ''
            nm_clean = nm.replace(' ', '')
            if nm_clean in SKIP_NAMES or not nm_clean:
                continue
            rt_raw = row[ratio_col].strip() if ratio_col < len(row) else ''
            majors.append({
                'nm':        nm_clean,
                'relate':    '',
                'stock_knd': '보통주',
                'stkqy':     row[qty_col].strip().replace(',', '') if qty_col != -1 and qty_col < len(row) else '',
                'stkrt':     rt_raw.replace('%', '').strip(),
            })

        if majors:
            break

    return {'majors': majors, 'small_summary': None}


def _extract_opinion(html: str) -> str:
    """감사의견 텍스트에서 의견 종류 추출 — 감사보고서 본문 + 사업보고서 표 형식 모두 지원"""
    text = _strip_tags(html)
    _MAP = {'적정의견': '적정', '한정의견': '한정', '부적정의견': '부적정',
            '의견거절': '의견거절', '적정': '적정', '한정': '한정', '부적정': '부적정'}
    # 우선순위 1: 부적정/한정/거절 명시 (false positive 방지 위해 먼저 검사)
    for kw in ('부적정의견', '한정의견', '의견거절'):
        if kw in text:
            return _MAP[kw]
    # 우선순위 2: 명시적 "적정의견"
    if '적정의견' in text:
        return '적정'
    # 우선순위 3: 사업보고서 표 형식 — "감사의견" 헤더 근방의 단독 의견 단어
    # 예: "감사의견 당기 OOO회계법인 적정 전기 ..."
    patterns = [
        r'감사\s*의견\s*[:：]\s*(적정|한정|부적정|의견거절)',
        r'감사\s*의견[\s\S]{0,200}?(?<![가-힣])(적정|한정|부적정|의견거절)(?![가-힣성한])',
        r'우리의\s*의견으로는[\s\S]{0,300}?(?<![가-힣])(적정|한정|부적정|의견거절)(?![가-힣성한])',
        r'(?:당기|전기|사업연도)[\s가-힣A-Za-z]{0,40}?회계법인\s*(?:[\s가-힣A-Za-z()0-9]{0,40})?(?<![가-힣])(적정|한정|부적정|의견거절)(?![가-힣성한])',
        r'감사인의\s*의견\s*[:：]?\s*(적정|한정|부적정|의견거절)',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = m.group(1).strip()
            return _MAP.get(raw, raw)
    # 우선순위 4: 무한정 적정 판단 — "공정하게 표시" 존재 + 부정 키워드 없음
    if '공정하게 표시' in text or '적정하게 표시' in text:
        if not any(k in text for k in ['한정의견', '부적정의견', '의견거절',
                                        '의견을 표명하지 않', '강조사항으로 인한 한정']):
            return '적정'
    return ''


def _extract_auditor(html: str) -> str:
    """감사인명 추출 — 공백 포함 "안 진 회 계 법 인" 형식 대응"""
    text = _strip_tags(html)
    # 공백 없는 형식: "삼일회계법인"
    m = re.search(r'([가-힣]{1,8}회계법인)', text)
    if m:
        return re.sub(r'\s+', '', m.group(1))
    # 공백 포함 형식: "안 진 회 계 법 인"
    m = re.search(r'([가-힣\s]{2,20}회\s*계\s*법\s*인)', text)
    if m:
        return re.sub(r'\s+', '', m.group(1))
    return ''


# ── DART 문서 목차 조회 ─────────────────────────────────────
async def _get_menu_elements(page, rcept_no: str) -> list[dict]:
    """main.do의 makeToc() JS에서 문서 구성요소 목록 반환.
    빈 응답(차단 의심) 시 최대 2회 재시도, 회차마다 대기 시간 증가."""
    url = f"{DART_BASE}/dsaf001/main.do?rcpNo={rcept_no}"
    for attempt in range(3):  # 최대 3회 시도
        html = ''
        try:
            await page.goto(url, wait_until="networkidle", timeout=35_000)
            html = await page.content()
        except Exception:
            pass

        # 페이지 내용이 너무 짧으면 → 차단/빈응답 의심
        if len(html) < 500:
            wait_sec = 20 + attempt * 20  # 20s → 40s → 60s
            if attempt < 2:
                print(f"  ⚠ main.do 빈응답 (rcpNo={rcept_no}) → {wait_sec}초 대기 후 재시도 ({attempt+1}/3)...", flush=True)
                await asyncio.sleep(wait_sec)
                continue
            return []  # 3회 모두 실패

        # makeToc() 내 nodeN 속성을 순서대로 추출해 zip
        ele_ids = re.findall(r"node\w+\['eleId'\]\s*=\s*['\"](\d+)['\"]", html)
        offsets = re.findall(r"node\w+\['offset'\]\s*=\s*['\"](\d+)['\"]", html)
        lengths = re.findall(r"node\w+\['length'\]\s*=\s*['\"](\d+)['\"]", html)
        dtds    = re.findall(r"node\w+\['dtd'\]\s*=\s*['\"]([^'\"]+)['\"]", html)
        dcm_nos = re.findall(r"node\w+\['dcmNo'\]\s*=\s*['\"](\d+)['\"]", html)
        texts   = re.findall(r"node\w+\['text'\]\s*=\s*\"([^\"]+)\"", html)

        items = []
        n = min(len(ele_ids), len(offsets), len(lengths), len(dtds), len(dcm_nos))
        for i in range(n):
            items.append({
                'ele_id': ele_ids[i],
                'offset': offsets[i],
                'length': lengths[i],
                'dtd':    dtds[i],
                'dcm_no': dcm_nos[i],
                'name':   texts[i] if i < len(texts) else f"element_{ele_ids[i]}",
            })
        return items
    return []


async def _fetch_element_html(page, rcept_no: str, ele: dict) -> str:
    """특정 문서 요소 HTML 반환. PDF eleId이면 빈 문자열."""
    if ele['dtd'].upper() == 'PDF':
        return ''
    url = (
        f"{DART_BASE}/report/viewer.do"
        f"?rcpNo={rcept_no}&dcmNo={ele['dcm_no']}&eleId={ele['ele_id']}"
        f"&offset={ele['offset']}&length={ele['length']}&dtd={ele['dtd']}"
    )
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        await asyncio.sleep(random.uniform(1.0, 2.0))  # 요소별 fetch 사이 딜레이
        return await page.content()
    except PWTimeout:
        return ''
    except Exception:
        return ''


async def _fetch_pdf_text_playwright(page, rcept_no: str, ele: dict) -> str:
    """Playwright 응답 인터셉션으로 DART PDF 바이트 수집 후 PyMuPDF 텍스트 추출.
    viewer.do 로딩 시 브라우저가 실제 PDF를 받아오는 네트워크 응답을 가로채는 방식.
    httpx URL 탐색보다 훨씬 신뢰성이 높음."""
    try:
        import fitz
    except ImportError:
        return ''

    viewer_url = (
        f"{DART_BASE}/report/viewer.do"
        f"?rcpNo={rcept_no}&dcmNo={ele['dcm_no']}&eleId={ele['ele_id']}"
        f"&offset={ele['offset']}&length={ele['length']}&dtd=PDF"
    )

    captured: list[bytes] = []

    async def on_response(response):
        ct = response.headers.get('content-type', '').lower()
        url_l = response.url.lower()
        if 'pdf' in ct or '.pdf' in url_l or '/pdf/' in url_l:
            try:
                body = await response.body()
                if body and len(body) > 10_000:
                    captured.append(body)
            except Exception:
                pass

    page.on('response', on_response)
    try:
        await page.goto(viewer_url, wait_until='networkidle', timeout=35_000)
        await asyncio.sleep(2)
    except Exception:
        pass
    finally:
        page.remove_listener('response', on_response)

    text = ''
    for pdf_bytes in captured[:1]:
        try:
            doc = fitz.open(stream=pdf_bytes, filetype='pdf')
            text += '\n'.join(p.get_text() for p in doc)
            doc.close()
        except Exception:
            pass
    return text


async def _dart_api_financial(client: httpx.AsyncClient, corp_code: str,
                               dart_key: str) -> tuple[dict, str]:
    """DART Open API fnlttSinglAcntAll 로 재무제표 조회.
    F001 회사도 DART DB에 구조화 데이터가 있으면 반환.
    반환: (계정 dict, bsns_year) | ({}, '')"""
    _ACCOUNT_MAP = {
        'assets':    {'자산총계'},
        'liab':      {'부채총계'},
        'equity':    {'자본총계'},
        'revenue':   {'매출액', '영업수익'},
        'op_income': {'영업이익', '영업이익(손실)'},
        'ni':        {'당기순이익', '당기순이익(손실)'},
    }

    for bsns_year in ('2024', '2023', '2022'):
        try:
            r = await client.get(
                'https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json',
                params={
                    'crtfc_key': dart_key,
                    'corp_code':  corp_code,
                    'bsns_year':  bsns_year,
                    'reprt_code': '11011',
                    'fs_div':     'OFS',
                },
                timeout=20,
            )
            d = r.json()
        except Exception:
            continue

        if d.get('status') in ('020', '010', '011'):
            break  # 키 한도 초과 또는 미등록
        if d.get('status') != '000' or not d.get('list'):
            continue

        result: dict[str, int] = {}
        for item in d['list']:
            nm = item.get('account_nm', '').strip()
            for key, names in _ACCOUNT_MAP.items():
                if key in result:
                    continue
                if nm in names:
                    val_str = str(item.get('thstrm_amount', '')).replace(',', '').strip()
                    if val_str and val_str not in ('-', ''):
                        try:
                            result[key] = int(float(val_str))
                        except (ValueError, TypeError):
                            pass
        if result:
            return result, bsns_year

    return {}, ''


def _detect_unit_text(text: str) -> int:
    """PDF 본문 텍스트에서 단위 감지"""
    m = re.search(r'단위\s*[:：]\s*(원|천\s*원|백만\s*원)', text)
    if m:
        u = m.group(1).replace(' ', '')
        if '백만' in u:
            return 1_000_000
        if '천' in u:
            return 1_000
    return 1


def _extract_fs_from_text(text: str, unit: int = 1) -> dict:
    """PDF 본문 텍스트에서 핵심 재무 계정 추출 (라인 단위 스캔)"""
    result: dict[str, int] = {}
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line_s = line.strip()
        for key, names in _FS_KEYS.items():
            if key in result:
                continue
            if not any(n in line_s for n in names):
                continue
            # 같은 줄에서 숫자 찾기
            nums = re.findall(r'-?[\d,]+', line_s)
            found = False
            for n in nums:
                v = _clean_num(n)
                if v is not None and abs(v) >= 1000:
                    result[key] = v * unit
                    found = True
                    break
            if not found:
                # 바로 다음 1~3줄에서 찾기
                for j in range(i + 1, min(i + 4, len(lines))):
                    nums2 = re.findall(r'-?[\d,]+', lines[j])
                    for n in nums2:
                        v = _clean_num(n)
                        if v is not None and abs(v) >= 1000:
                            result[key] = v * unit
                            found = True
                            break
                    if found:
                        break
    return result


# ── 핵심 스크레이핑 함수 ────────────────────────────────────
async def scrape_one(page, corp_code: str, rcept_no: str,
                     stlm: str, rcept_dt: str,
                     dart_client: httpx.AsyncClient | None = None,
                     dart_key: str = '') -> str:
    """
    DART 감사보고서 1건 파싱.
    반환값: 'cached'|'scraped'|'pdf_parsed'|'dart_api'|'pdf_fail'|'no_menu'|'error'
    """
    out = PERM_DIR / f"{corp_code}_{rcept_no}.json"
    if out.exists():
        return 'cached'

    async with SEM:
        # 1) 문서 목차 조회
        elements = await _get_menu_elements(page, rcept_no)
        if not elements:
            return 'no_menu'

        # PDF 전용 문서 → ① Playwright 응답 인터셉션 → ② DART API fallback
        if all(e['dtd'].upper() == 'PDF' for e in elements):
            result = {
                'corp_code':   corp_code,
                'rcept_no':    rcept_no,
                'stlm':        stlm,
                'rcept_dt':    rcept_dt,
                'scraped_at':  TODAY,
                'source':      'playwright_pdf',
                'audit':       {'opinion': '', 'auditor': '', 'stlm_dt': stlm.replace('.', '')[:6] + '31'},
                'financials':  {'OFS': None, 'CFS': None},
                'shareholder': {'majors': [], 'small_summary': None},
            }

            # ① Playwright PDF 인터셉션 (브라우저가 받는 네트워크 응답 가로채기)
            all_text = ''
            for ele in elements[:3]:
                txt = await _fetch_pdf_text_playwright(page, rcept_no, ele)
                if txt:
                    all_text += '\n' + txt
                    break  # 한 PDF에서 텍스트 얻으면 충분

            if all_text.strip():
                op = _extract_opinion(all_text)
                if op:
                    result['audit']['opinion'] = op
                    result['audit']['auditor']  = _extract_auditor(all_text)
                unit = _detect_unit_text(all_text)
                fs_data = _extract_fs_from_text(all_text, unit)
                if fs_data:
                    result['financials']['OFS'] = fs_data
                if fs_data or op:
                    PERM_DIR.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                    return 'pdf_parsed'

            # ② DART Open API fallback (구조화 데이터가 있는 F001 기업용)
            if dart_client and dart_key:
                fs_data, bsns_year = await _dart_api_financial(dart_client, corp_code, dart_key)
                if fs_data:
                    result['source'] = 'dart_api'
                    result['financials']['OFS'] = fs_data
                    result['financials']['bsns_year'] = bsns_year
                    PERM_DIR.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                    return 'dart_api'

            return 'pdf_fail'

        result = {
            'corp_code':  corp_code,
            'rcept_no':   rcept_no,
            'stlm':       stlm,
            'rcept_dt':   rcept_dt,
            'scraped_at': TODAY,
            'source':     'playwright_html',
            'audit':      {'opinion': '', 'auditor': '', 'stlm_dt': stlm.replace('.', '')[:6] + '31'},
            'financials': {'OFS': None, 'CFS': None},
            'shareholder': {'majors': [], 'small_summary': None},
        }

        all_fs_tables:  list = []
        all_sh_tables:  list = []
        fs_unit = 1
        opinion_found = False

        # 2) 섹션별 파싱
        for ele in elements:
            if ele['dtd'].upper() == 'PDF':
                continue

            name = ele['name']
            name_clean = name.replace(' ', '')  # "재 무 상 태 표" → "재무상태표"
            is_audit_report  = any(k in name_clean for k in ['감사인', '감사보고서']) and '재무' not in name_clean
            is_fs_section    = any(k in name_clean for k in ['재무상태표', '손익계산서', '포괄손익', '재무제표', '자본변동', '현금흐름'])
            is_notes_section = '주석' in name_clean

            if not (is_audit_report or is_fs_section or is_notes_section):
                continue

            html = await _fetch_element_html(page, rcept_no, ele)
            if not html:
                continue

            # 감사의견 추출
            if is_audit_report and not opinion_found:
                op = _extract_opinion(html)
                if op:
                    result['audit']['opinion'] = op
                    result['audit']['auditor']  = _extract_auditor(html)
                    opinion_found = True

            # 재무제표 테이블 수집
            if is_fs_section:
                unit = _detect_unit(html)
                if unit > fs_unit:
                    fs_unit = unit
                all_fs_tables.extend(_parse_tables(html))

            # 주석 테이블 수집
            if is_notes_section:
                all_sh_tables.extend(_parse_tables(html))

        # 3) 재무 계정 추출 (연결/별도 자동 판단)
        fs_data = _extract_fs_from_tables(all_fs_tables, fs_unit)
        if fs_data:
            result['financials']['OFS'] = fs_data   # 감사보고서는 별도 기준이 기본

        # 4) 주주현황 추출
        sh_data = _extract_shareholder_from_tables(all_sh_tables)
        if sh_data['majors']:
            result['shareholder'] = sh_data

        # 5) 저장 (재무 또는 주주 중 하나라도 있으면)
        if fs_data or sh_data['majors'] or opinion_found:
            PERM_DIR.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            return 'scraped'

        return 'error'


# ── 메인 ──────────────────────────────────────────────────
async def main(limit: int = 0) -> None:
    print("[1/4] 회사 목록 로드 중...", flush=True)
    _pm = os.environ.get("PORTAL_MASTER")
    if _pm:
        _raw = json.loads(Path(_pm).read_text(encoding="utf-8-sig"))
        _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
    else:
        if FULL_MASTER.exists():
            _raw = json.loads(FULL_MASTER.read_text(encoding="utf-8"))
            _comps = _raw.get("companies", _raw) if isinstance(_raw, dict) else _raw
        else:
            print("[오류] PORTAL_MASTER 환경변수 또는 company_master_full.json 필요", flush=True)
            return

    # F001 기업
    f001_comps = [c for c in _comps if c.get("dart_data_level") == "f001" and c.get("corp_code")]

    # ★ A001 빈 의견 자동 감지 — DART 정형 API가 응답 스키마 누락한 회사들
    #   permanent 캐시(영구)에서 모든 연도의 audit.opinion이 비어있는 a001 회사 추출
    a001_codes = {c["corp_code"] for c in _comps if c.get("dart_data_level") == "a001"}
    by_corp_opinions: dict[str, list[str]] = {}
    if FIN_PERM_DIR.exists():
        for p in FIN_PERM_DIR.glob("*.json"):
            stem = p.stem
            if "_" not in stem:
                continue
            cc, _yr = stem.rsplit("_", 1)
            if cc not in a001_codes:
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            op = (d.get("audit") or {}).get("opinion", "") or ""
            by_corp_opinions.setdefault(cc, []).append(op)
    a001_empty_codes = {
        cc for cc, ops in by_corp_opinions.items()
        if ops and all(not op for op in ops)
    }
    a001_empty_comps = [c for c in _comps if c.get("corp_code") in a001_empty_codes]

    if limit:
        f001_comps = f001_comps[:limit]
        a001_empty_comps = a001_empty_comps[:limit]
    print(f"  F001 기업: {len(f001_comps)}개사 / A001 빈 의견 보강: {len(a001_empty_comps)}개사", flush=True)

    # 보고서 캐시 파일 목록 한 번에 읽기 (회사별 glob 반복 방지)
    print("[2/4] 보고서 캐시 인덱싱 중...", flush=True)
    reports_index: dict[str, Path] = {}
    for p in REPORTS_CACHE_DIR.glob("*_reports_*.json"):
        cc = p.name.split("_reports_")[0]
        if cc not in reports_index or p.name > reports_index[cc].name:
            reports_index[cc] = p

    # 추출 대상 목록 구성 (F001 + A001 빈 의견 보강)
    print("[3/4] 추출 대상 목록 구성 중...", flush=True)
    targets = []
    for c in (f001_comps + a001_empty_comps):
        cc = c["corp_code"]
        rfile = reports_index.get(cc)
        if not rfile:
            continue
        d = json.loads(rfile.read_text(encoding="utf-8"))
        items = [it for it in d.get("items", []) if it.get("kind") in ("감사보고서", "사업보고서")]
        if not items:
            continue
        for item in items[:3]:  # 최대 3개년 보고서
            targets.append({
                "corp_code": cc,
                "rcept_no":  item["rcept_no"],
                "stlm":      item.get("stlm", ""),
                "rcept_dt":  item.get("rcept_dt", ""),
            })

    cached_count = sum(1 for t in targets if (PERM_DIR / f"{t['corp_code']}_{t['rcept_no']}.json").exists())
    print(f"[4/4] 추출 대상: {len(targets)}건 / 이미 완료: {cached_count}건 / 신규: {len(targets)-cached_count}건", flush=True)

    # PyMuPDF 확인
    try:
        import fitz  # noqa: F401
        print("  PyMuPDF(fitz) 확인 — Playwright PDF 인터셉션 활성화", flush=True)
    except ImportError:
        print("  ⚠ PyMuPDF 미설치 — pip install pymupdf", flush=True)

    stats = {'cached': 0, 'scraped': 0, 'pdf_parsed': 0, 'dart_api': 0,
             'pdf_fail': 0, 'no_menu': 0, 'error': 0}

    # DART API 키 (dart_key_manager 없으면 단일 키 사용)
    try:
        from dart_key_manager import DartKeyManager
        _km = DartKeyManager(CRED)
        _dart_key = _km.current_key
    except Exception:
        _dart_key = CRED.get("dart", {}).get("api_key", "")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )

        async with httpx.AsyncClient(verify=False, timeout=30) as dart_client:
            new_count = 0  # 신규 처리 건수 (캐시 제외) — 주기 휴식 판단용
            for i, t in enumerate(targets, 1):
                page = await context.new_page()
                try:
                    status = await scrape_one(
                        page, t["corp_code"], t["rcept_no"], t["stlm"], t["rcept_dt"],
                        dart_client=dart_client,
                        dart_key=_dart_key,
                    )
                    stats[status] = stats.get(status, 0) + 1
                    if status != 'cached':
                        new_count += 1
                except Exception as e:
                    stats["error"] += 1
                    new_count += 1
                    status = f"error({e})"
                finally:
                    await page.close()

                if i % 10 == 0 or i == len(targets):
                    print(
                        f"  진행: {i}/{len(targets)} | "
                        f"HTML:{stats['scraped']} PDF:{stats['pdf_parsed']} "
                        f"API:{stats['dart_api']} 캐시:{stats['cached']} "
                        f"차단/메뉴없음:{stats['no_menu']} 실패:{stats['pdf_fail']} 오류:{stats['error']}",
                        flush=True,
                    )

                if status == 'cached':
                    await asyncio.sleep(0.2)  # 캐시는 빠르게 통과
                else:
                    # 보고서 사이 랜덤 딜레이 (4~8초)
                    await asyncio.sleep(random.uniform(4, 8))

                # 신규 20건마다 60초 휴식 (DART IP 차단 방지)
                if new_count > 0 and new_count % 20 == 0:
                    print(f"  ⏸ {new_count}건 처리 — DART 서버 부하 방지를 위해 60초 휴식 중...", flush=True)
                    await asyncio.sleep(60)
                    new_count = 0  # 카운터 리셋

        await browser.close()

    print(
        f"\n완료 — HTML:{stats['scraped']} PDF:{stats['pdf_parsed']} API:{stats['dart_api']} "
        f"캐시:{stats['cached']} 차단/메뉴없음:{stats['no_menu']} "
        f"PDF실패:{stats['pdf_fail']} 오류:{stats['error']}",
        flush=True,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        import subprocess
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("Playwright chromium 설치 완료")
    else:
        limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
        asyncio.run(main(limit))
