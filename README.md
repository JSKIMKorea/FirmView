# 종합기업정보 Portal

DART 전자공시시스템 공시 회사들의 감사·자문 핵심 정보를 한 화면에서 조회하는 **PwC 내부 전용** 정적 웹 포털.

- **배포**: GitHub Pages (https://jskimkorea.github.io/FirmView/) — 정적 호스팅
- **인증**: PwC 이메일 + 사번 6자리, bcrypt 해시 검증
- **데이터**: DART · KOSIS · 국세청 · 네이버 · 카카오 · KRX · Seibro
- **빌드 산출물**: 단일 `index.html` (모든 데이터·라이브러리 인라인 포함)

---

## 수집 대상 기업 모집단 (총 7,344개)

`dart_master.py`가 **A001(사업보고서) 전체 ∪ Retain Viewer 전체** 합산.

| 수준 | 개수 | Retain | 비Retain | 수집 범위 |
|---|---|---|---|---|
| **a001** | 3,476 | 1,096 | 2,380 | 재무·감사의견·주주·공시·주가 풀데이터 |
| **f001** | 2,414 | 2,414 | 0 | F001 감사보고서 Playwright HTML 파싱 |
| **basic** | 786 | 786 | 0 | 기업개황·뉴스·국세청·KOSIS만 |
| **none** | 668 | 668 | 0 | 검색 자동완성만 (수집 스킵) |

---

## 기업 유형별 데이터 추출 매트릭스

| # | 카테고리 | a001 | f001 | basic | none |
|---|----------|------|------|-------|------|
| 1 | 기업 기본정보 | DART `company.json` | DART `company.json` | DART `company.json` | ❌ |
| 2 | 정기공시 보고서 목록 | DART `list.json` 사업·감사·반기·분기 | DART `list.json` 감사·사업 | ❌ | ❌ |
| 3 | 재무제표 (CFS+OFS) | DART `fnlttSinglAcntAll.json` 11011 | Playwright 감사보고서 HTML | ❌ | ❌ |
| 4 | **감사의견** | DART `accnutAdtorNm...` ★Playwright fallback (49건) | Playwright HTML | ❌ | ❌ |
| 5 | 강조사항·KAM | DART `accnutAdtorNm...` | Playwright (부분) | ❌ | ❌ |
| 6 | 대량보유 5%↑ | DART `majorstock.json` | DART `majorstock.json` | ❌ | ❌ |
| 7 | 주주현황 (전체) | DART `hyslrSttus.json` 주석 | Playwright HTML | ❌ | ❌ |
| 8 | 소액주주 합계 | DART `mrhlSttus.json` (상장) | ❌ | ❌ | ❌ |
| 9 | 주가 현재가 | yfinance(KRX)→금융위 (상장) · 상증세법 §63 (비상장) | 동일 | yfinance만 | ❌ |
| 10 | BPS · PBR | 별도 자본총계÷주식수 (DART OFS) | 별도÷주식수 (Playwright) | ❌ | ❌ |
| 11 | 외국인 지분율 | KRX `MDCSTAT03702` (상장) | KRX (상장) | KRX (상장) | ❌ |
| 12 | 동종업계 | KOSIS API (KSIC 2자리→대분류→전산업) | 동일 | ❌ | ❌ |
| 13 | 국세청 사업자상태 | 공공데이터포털 POST | 동일 | 동일 | ❌ |
| 14 | 뉴스 | 네이버 검색 API (10건) | 동일 | 동일 | ❌ |
| 15 | 세무조사 이력 | DART 키워드 + 네이버 이중필터 | 동일 | 네이버만 | ❌ |
| 16 | 신용등급 | UI 수동 입력 | UI 수동 입력 | UI 수동 입력 | ❌ |
| 17 | 회사채 이자율 | Seibro Playwright (전사 공통) | 동일 | 동일 | ❌ |
| 18 | 카카오맵 위치 | DART `adres` 지오코딩 | 동일 | 동일 | ❌ |
| 19 | **Global Retain 정보** | UI 외부 링크 (Engagement·ET) | 동일 | 동일 | 동일 |

### A001 감사의견 누락 49건 (≈0.9%) — 자동 보강 18건 완료

DART `accnutAdtorNmNdAdtOpinion.json`이 회사별 응답 스키마 불일치 — 백산 등 49개사에서 `adt_opinion`/`emphs_matter`/`core_adt_matter` 3개 필드가 응답에 아예 없음. `adtor='-'` placeholder만 반환.

→ **자동 보강**: `playwright_f001.py`가 `permanent/{cc}_{YYYY}.json` 스캔 → 모든 연도 빈 의견인 a001 자동 감지 → 사업보고서 본문 직접 파싱 → `cache/12.감사보고서_Playwright/`. `build_portal.py`가 빈 연도에만 머지(`audit.source:"playwright"`). **현재 18개사 보강 완료** (백산·롯데건설·호텔롯데·한섬·이랜드월드·서한·에스지신성건설·하나35호스팩 등). 16개사는 사업보고서 자체 미제출(SK스퀘어/SK온/스팩 등), 1개사는 보고서 형식 특이로 추후 패턴 보강 예정.

### 캐시 파일 명명 규칙

```
cache/00.회사목록/                company_master_full.json
cache/01.기업개황/                {corp_code}_company_{YYYYMMDD}.json (당일 1파일)
cache/02.재무제표_감사의견/       {corp_code}_financial_{YYYYMMDD}.json
                                 + permanent/{corp_code}_{YYYY}.json (영구)
cache/03.주가_BPS_PBR/            {corp_code}_stock_{YYYYMMDD}.json
cache/04.대량보유/                {corp_code}_majorstock_{YYYYMMDD}.json
cache/05.뉴스/                    {corp_code}_news_{YYYYMMDD}.json
cache/06.국세청_사업자상태/       {corp_code}_tax_{YYYYMMDD}.json
cache/07.업종통계_KOSIS/          {corp_code}_industry_{YYYYMMDD}.json
cache/08.정기공시보고서/          {corp_code}_reports_{YYYYMMDD}.json
cache/09.주주현황_보고서주석/     {corp_code}_hyslr_{YYYYMMDD}.json
cache/10.세무조사이력/            {corp_code}_taxinv_{YYYYMMDD}.json
cache/11.회사채만기수익률_Seibro/ bond_yields_{YYYYMMDD}.json (전사 공통)
cache/12.감사보고서_Playwright/   {corp_code}_{rcept_no}.json (보고서별 영구)
```

---

## 프로젝트 구조

```
종합기업정보/
├─ 1.회사명정리/                  # [입력] 회사 마스터 (read-only 원본)
├─ 2.사용자관리/users.xlsx        # [입력] 로그인 사용자 (Git 금지·사번 평문)
├─ 3.포털빌드/                    # [메인]
│  ├─ 1.수집/
│  │  ├─ 00.회사목록/dart_master.py            # A001 ∪ Retain Viewer 유니버스
│  │  ├─ 01.DART_공시/                         # dart_key_manager / company / financial /
│  │  │                                       # shareholder / shareholder_full / reports /
│  │  │                                       # playwright_f001 (F001 + a001 49건 보강)
│  │  ├─ 02.주가/stock.py                      # yfinance + 금융위 + 상증세법 + KRX 외국인
│  │  ├─ 03.뉴스/news.py
│  │  ├─ 04.세무/tax_status.py / tax_investigation.py
│  │  ├─ 05.KOSIS_업종통계/industry.py
│  │  ├─ 06.Seibro_회사채이자율/bond_yields.py # Playwright 일별 시계열
│  │  └─ collect.py                            # 전체 수집 (--from STEP으로 특정 단계부터 재시작)
│  ├─ 2.빌드/build_portal.py
│  ├─ 3.운영/update_users.py / deploy_git.py   # git push 방식 (62MB 초과 대응)
│  ├─ templates/portal.html
│  ├─ cache/                                   # gitignored, 12개 하위 폴더
│  └─ output/index.html                        # gitignored
├─ credentials.json                            # Git 금지
├─ PRD.md / README.md / CLAUDE.md
```

HTML 플레이스홀더: `__PORTAL_DATA_JSON__`, `__PORTAL_USERS_JSON__`, `__KAKAO_JS_KEY__`, `__BUILD_TIME__`, `/*__BCRYPT_JS__*/`, `/*__CHART_JS__*/`

---

## 초기 설정

### 1. 패키지 설치
```bash
pip install httpx pandas openpyxl yfinance bcrypt pyodbc python-dotenv playwright curl_cffi pymupdf
python -m playwright install chromium    # Seibro·F001/a001 보강용 (1회)
```

### 2. credentials.json (프로젝트 루트)
```json
{
  "dart":     { "api_key": "기본키", "api_keys": ["키1","키2","키3","키4","키5","키6"] },
  "kosis":    { "api_key": "..." },
  "odcloud":  { "api_key": "..." },
  "naver":    { "client_id": "...", "client_secret": "..." },
  "kakao":    { "js_key": "..." },
  "azure_sql":{ "server": "...", "database": "...", "username": "...", "password": "..." },
  "github":   { "token": "ghp_...", "repo": "JSKIMKorea/FirmView", "branch": "main", "path": "index.html" },
  "gh_log":   { "token": "github_pat_...", "repo": "JSKIMKorea/firmview-login-log" }
}
```
> Kakao Maps: Developers Console → 카카오맵 활성화 + 사이트 도메인 등록 필수.
> `gh_log`: 로그인 성공 1회당 Private 저장소 `log.csv` 1행 prepend (선택사항). 사전에 빈 저장소 생성 + Push protection Disabled 필요. 토큰은 Fine-grained PAT(대상 저장소 1개, Contents R/W).

### 3. 사용자 계정 (`2.사용자관리/users.xlsx`)
- 시트 `azure_auto`(Azure SQL 동기화 — 수기 편집 금지) + `manual_add`(수기 추가 보존)
- 컬럼: `이메일 / 사번 / 이름 / 부서 / 활성(Y/N)`
- 동기화: `python 3.포털빌드/3.운영/update_users.py` (Azure SQL `BI_STAFFREPORT_EMP_V`)

---

## 실행 방법

```bash
cd 3.포털빌드

# 전체 (수집 6~10h + 빌드 + 배포)
python collect_all.py

# 수집 단계별
python 1.수집/collect.py                # 전체 수집 (캐시 이어서)
python 1.수집/collect.py --test 50      # 테스트 50개사
python 1.수집/collect.py --skip-bond    # Seibro 스킵

# 특정 단계부터 재시작 (--from STEP)
python 1.수집/collect.py --from financial
# STEP: master / company / financial / shareholder / stock / news /
#       tax-status / kosis / reports / shareholder-full / tax-investigation

# 빌드·배포 분리
python 2.빌드/build_portal.py --skip-upload   # 빌드만
python 3.운영/deploy_git.py                   # GitHub Pages 배포 (git push)
python collect_all.py --build-only            # 수집 스킵, 빌드+배포만
```

> `deploy_git.py`는 `git clone --depth=1` + 파일 복사 + `git push` 방식 — GitHub Contents API의 ~50MB 제한 우회.

---

## Seibro 회사채이자율 (Playwright 일별)

신용등급 카드의 등급별 무보증 회사채 이자율은 한국예탁결제원 Seibro에서 영업일 기준 매일 추출.

- **URL**: `seibro.or.kr/.../BIP_CNTS03030V.xml` 채권 > 채권만기수익률
- **추출**: 무보증 공모 회사채 (AAA~BBB-, 10등급) + 국채(양곡·외평·재정), 8개 만기 (3M·6M·9M·1Y·3Y·5Y·10Y·20Y)
- **저장**: `cache/11.회사채만기수익률_Seibro/bond_yields_{YYYYMMDD}.json`
- **시작 시점**: 2023-01-01 / **백필 완료**: 2023-01 ~ 2026-04 (40개월)
- 비영업일 sentinel `{"skipped":true}` — 재시도 안 함
- Portal 매칭: 평가일 ↔ 같은 연·월 월말 자료 자동 적용, 매칭 실패 시 가장 가까운 이전 월말

```bash
python "1.수집/06.Seibro_회사채이자율/bond_yields.py"           # 누락된 영업일만
python "1.수집/06.Seibro_회사채이자율/bond_yields.py" 2026-05-04  # 단일 일자
python "1.수집/06.Seibro_회사채이자율/bond_yields.py" 2023-01 2023-12  # 월 범위
```

---

## 로그인 시스템 (세션 쿠키)

> **창 닫지 않는 한 유지, 창 종료 시 자동 로그아웃.**

| 시나리오 | 결과 |
|---|---|
| 탭 닫기·재접속 / 새 탭 / Ctrl+F5 | ✅ 자동 로그인 |
| 로그아웃 버튼 / 창 종료 / 시크릿 / `users.xlsx` 비활성화 후 재빌드 | ❌ 재로그인 |

**구현**: `expires`/`max-age` 없는 세션 쿠키 → 페이지 로드 시 PORTAL_USERS 재검증 (퇴사 안전장치). 빌드 시 `users.xlsx` 사번을 bcrypt $2a$ 해시로 변환·HTML 임베드 (평문 미저장). 런타임에서 `bcryptjs`(인라인 ~21KB)가 입력 사번을 해시와 비교.

**회사망 대응**: `bcryptjs`·`Chart.js` 인라인 임베드, Kakao SDK 차단 시 외부 지도 링크 fallback, Google Fonts 차단 시 시스템 sans-serif fallback.

---

## 주요 구현 로직 요약

### F-01 내부회계관리제도 (외감법 §8)
| 자산총계 (직전말 별도재무제표) | 결과 |
|---|---|
| 상장 ≥1,000억 | 감사 / 상장 <1,000억 | 검토 |
| 비상장 금융(KSIC 64/65/66) ≥1,000억 | 검토 |
| 비상장 일반 ≥5,000억 | 검토 / 그 외 | 해당없음 + 담당자 직접확인 |

자산: 최신 OFS 우선 → 없으면 이전 연도 폴백. 연결 IC는 `ofs_assets or cfs_assets` + `has_cfs` 자동 산출.

### F-02 감사의견·재무
- DART `accnutAdtorNmNdAdtOpinion` (감사인·감사의견·강조사항·KAM)
- DART `fnlttSinglAcntAll` 사업보고서(11011) 기준 CFS+OFS, 전년 ▲▼ 자동
- 동적 수집 연도: `[현재년도-1, -2, -3]`
- A001 49건 빈 의견: `playwright_f001.py` 자동 인식 → 본문 직접 파싱
- F001: 정형 API 부재 → `playwright_f001.py` 감사보고서 HTML 파싱

### F-04 동종업계 (KOSIS)
1. KSIC 2자리 중분류 (37개) → 2. KSIC 대분류 letter (A~S) → 3. 전산업 평균

### F-05 주가·밸류에이션·배당
- 상장: yfinance(KRX) + curl_cffi Chrome TLS 임퍼소네이션 → 금융위 fallback
- 확장 필드: 목표주가·애널리스트수·추천등급·배당수익률·배당금·배당락일
- 비상장: 상증세법 §63 = (순자산가치×2 + 순이익가치×3)÷5 (가중평균 EPS÷0.10, 3:2:1)
- BPS = 별도 자본총계÷발행주식수 / 주식수 우선: `isu_shr` → `stockTotqySttus.istc_totqy`
- PBR 시각화 (0~3x, 1x 표시): ≥1 초록 "손상징후 없음" / <1 빨강 "검토 필요"
- KRX 외국인: `data.krx.co.kr` `MDCSTAT03702` (상장 only)

### F-06 주주현황
- 메인: `hyslrSttus.json` (사업보고서 「주주에 관한 사항」 주석)
- 보조: `mrhlSttus.json` (상장 소액주주 합계). 합계<100% 시 "기타" 자동 추가

### F-07~F-11
- **제재**: DART + 공정위 외부 링크
- **국세청**: 공공데이터포털 사업자상태 (계속/휴업/폐업)
- **세무조사**: DART 키워드 공시 + 네이버(제목에 키워드 + 제목/요약에 회사명 별칭)
- **법원등기**: 인터넷등기소 외부 링크 (열람 700원/발급 1,000원)
- **위치**: 카카오맵 240px 내장, 실패 시 외부 지도 링크

---

## 디자인 시스템 — PwC 팔레트

```
Orange:  #FD5108 · #FE7C39 · #FFAA72 · #FFCDA8 · #FFE8D4 · #FFF5ED · #C44608(deep)
Grey:    #A1A8B3 · #B5BCC4 · #CBD1D6 · #DFE3E6 · #EEEFF1 · #F5F7F8
의미색:  #2E7D32(양호 초록) · #C62828(위험 빨강) · #3B82F6(인디고)
```
폰트 `'Noto Sans KR'` 단일. 라이트/다크 토글 (`localStorage`). **헤더 띠**: `#FE7C39` (Orange 400, 한 단계 연함) + 텍스트 `#000000` (검정 가독성).

### 톤다운 — 주황 절제 (★ 표준)
- **흰색·옅은 회색 베이스**, 카드 배경은 흰색/`var(--bg-elev)`(#F5F7F8) 위주
- CSS 시멘틱 변수: `--green`=#2E7D32 / `--red`=#C62828 / `--bg-elev`=옅은 회색 (이전: 옅은 오렌지)
- 텍스트는 검정/회색 기본, 오렌지(`pwc-orange-deep`)는 **핵심 강조에만**
- IC/세무/TI 결과 카드: 강한 그라데이션 → 옅은 단색
- 그래프 색 통일: 음수일 때도 `var(--pwc-orange)` (방향은 ▲▼로만 구분)
- 모든 숫자 **천단위 콤마 필수** — `toLocaleString('ko-KR')` 또는 `fmtNum(n)`

### Brush-stroke 강조

박스/colored border 대신 `::before` brush:
```css
.elem{position:relative;isolation:isolate;padding:2px 14px 2px 4px}
.elem::before{content:'';position:absolute;inset:0;
  background:linear-gradient(108deg,rgba(R,G,B,.18) 0%,rgba(R,G,B,.08) 55%,transparent 100%);
  z-index:-1;transform:skewX(-5deg);border-radius:3px}
```

**색**: 적정·동종업계=초록(34,160,70) / 한정·의견거절=핑크(210,50,70) / 별도·연결·현재가=오렌지(253,81,8) / 주가 메트릭=보라(100,80,200) / IC·재무 헤드=인디고(59,130,246)

**금지**: 장식용 colored border-left/border-top 신규 추가 (TOC active만 예외). 로고 배너 흰 박스 금지 — 72×44 + 옅은 오렌지 brush. 더미 주소(`xx`/`xxx`/`예시`/`sample`) 카카오맵 SDK 호출 차단.

### 카드 14개 + 사이드바 4개 그룹
**Company Overview** 기업 기본정보·내부회계·감사의견 / **Financial Analysis** 재무제표·재무 요약·동종업계·신용등급·주가·주주현황 / **Compliance & Legal** 제재·국세청·세무조사·뉴스·법원등기 / **PwC Reference** Global Retain 정보(외부 링크)

---

## API 키 / 보안

- 발급: DART(`opendart.fss.or.kr`) / KOSIS(`kosis.kr/openapi`) / 국세청(`data.go.kr` 1~2일) / 네이버(`developers.naver.com`) / 카카오(`developers.kakao.com` — Web 도메인 등록 + 카카오맵 활성화) / GitHub(repo 권한 토큰) — 모두 무료
- `users.xlsx`·`credentials.json` Git 업로드 절대 금지. HTML에는 사번 bcrypt 해시만(평문 미포함)
- 모든 데이터 **참고용** — 최종 회계·감사 의사결정은 원본 공시 직접 확인. 사업보고서 제출대상·공시대상기업집단 자동확인 불가 — 담당자 검토 필요

---

## 기술 스택

| 구분 | 기술 |
|---|---|
| 수집 | Python 3.11+ / `httpx` 비동기 (`dart_master.py`만 `requests` 동기) |
| Playwright | Seibro 일별·F001/a001 보강 HTML 파싱 |
| 인증 | `bcrypt`(빌드) / `bcryptjs` 인라인(런타임) |
| 프론트 | Vanilla HTML/CSS/JS · Chart.js · Kakao Maps · `'Noto Sans KR'` |
| 주가 | yfinance + curl_cffi Chrome TLS / 금융위 / KRX |
| 날씨 | wttr.in (47개 지역) |
| 배포 | GitHub Pages — `deploy_git.py` git push 방식 (파일 크기 무제한) |
| Azure | pyodbc (`BI_STAFFREPORT_EMP_V`) |
