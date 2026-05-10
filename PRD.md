# PRD — 종합기업정보 검색 포털

**버전** 0.7 | **작성일** 2026-05-10

---

## 1. 배경 및 목적

PwC 담당자가 감사·자문 대상 기업을 검색하여 감사 수임 판단·절차 계획에 필요한 핵심 정보를 한 화면에서 확인할 수 있는 내부 포털.

**기초 모집단**: A001(사업보고서) 전체 ∪ Retain Viewer 전체 — `dart_master.py`가 매 수집 전 자동 생성

| 수준 | 합계 | Retain | 비Retain | 수집 내용 |
|---|---|---|---|---|
| a001 | 3,476 | 1,096 | 2,380 | 재무·감사의견·주주·공시 풀데이터 |
| f001 | 2,414 | 2,414 | 0 | F001 감사보고서 Playwright HTML |
| basic | 786 | 786 | 0 | 기본정보 + 뉴스만 |
| none | 668 | 668 | 0 | 검색 표시만 (수집 스킵) |
| **합계** | **7,344** | **4,964** | **2,380** | |

> f001·basic·none은 전부 Retain Viewer 소속. 비Retain a001 2,380개는 사업보고서 제출 기업 중 Retain 미등록 — 동일 풀데이터 적용.

---

## 2. 사용자

| 구분 | 설명 |
|---|---|
| 주 사용자 | 감사팀 / 자문팀 회계사 |
| 환경 | 사내 PC 브라우저 (Chrome/Edge) |
| 인증 | 이메일(ID) + 사번 6자리(PW) 로그인 필수 |
| 계정 관리 | 관리자가 `users.xlsx` 직접 추가·삭제 |

---

## 3. 시스템 아키텍처 (정적 생성)

```
[dart_master.py] → company_master_full.json (dart_data_level 분류)
        ↓
[1.수집/collect.py]  레벨별 수집 범위 결정
  company / financial / shareholder / shareholder_full / reports
  / playwright_f001 / stock / news / tax_status / industry / bond_yields
        ↓ cache/ 폴더에 {corp_code}_{종류}_{YYYYMMDD}.json 저장
[2.빌드/build_portal.py]
  users.xlsx → bcrypt 해시 (사번 원문 소멸)
  cache/*.json → PORTAL_DATA 조립
  templates/portal.html 플레이스홀더 치환
        ↓
[output/index.html]  단일 HTML (데이터 내장)
        ↓ deploy_git.py git push (62MB 초과 대응)
[GitHub Pages]
        ↓
[브라우저]  bcryptjs 사번 검증 → 세션 쿠키 → 클라이언트 렌더링
```

- **인증**: 빌드 시 bcrypt 해시 → HTML 내장 → bcryptjs 검증 → 세션 쿠키
- **API 키**: Python 수집에서만 사용, HTML 미포함
- **사용자 파일**: `users.xlsx` 로컬 전용, 빌드 시 해시만 추출

---

## 4. 기능 요구사항

### F-AA. 기업 유형별 데이터 추출 매트릭스 (모든 F-XX에 우선 적용)

| # | 데이터 | a001 | f001 | basic | none |
|---|--------|------|------|-------|------|
| 1 | 기업 기본정보 | DART `company.json` | DART `company.json` | DART `company.json` | ❌ |
| 2 | 정기공시 보고서 목록 | DART `list.json` 사업·감사·반기·분기 | DART `list.json` 감사·사업 | ❌ | ❌ |
| 3 | 재무제표 (CFS+OFS) | DART `fnlttSinglAcntAll.json` | Playwright HTML | ❌ | ❌ |
| 4 | 감사의견 | DART `accnutAdtorNm...` + Playwright fallback (49건) | Playwright HTML | ❌ | ❌ |
| 5 | 강조사항·KAM | DART `accnutAdtorNm...` | Playwright (부분) | ❌ | ❌ |
| 6 | 대량보유 5%↑ | DART `majorstock.json` | DART `majorstock.json` | ❌ | ❌ |
| 7 | 주주현황 (전체) | DART `hyslrSttus.json` | Playwright HTML | ❌ | ❌ |
| 8 | 소액주주 합계 | DART `mrhlSttus.json` (상장만) | ❌ | ❌ | ❌ |
| 9 | 주가 현재가 | yfinance/금융위 (상장)·상증세법 (비상장) | 동일 | yfinance만 | ❌ |
| 10 | BPS·PBR | 별도 자본총계÷주식수 (DART OFS) | 별도÷주식수 (Playwright) | ❌ | ❌ |
| 11 | 외국인 지분율 | KRX `MDCSTAT03702` (상장) | KRX (상장) | KRX (상장) | ❌ |
| 12 | 동종업계 | KOSIS API (KSIC 2자리) | KOSIS API | ❌ | ❌ |
| 13 | 국세청 사업자상태 | 공공데이터포털 POST | 동일 | 동일 | ❌ |
| 14 | 뉴스 | 네이버 검색 API (10건) | 동일 | 동일 | ❌ |
| 15 | 세무조사 이력 | DART 키워드 + 네이버 이중필터 | 동일 | 네이버만 | ❌ |
| 16 | 신용등급 | UI 수동 입력 | UI 수동 입력 | UI 수동 입력 | ❌ |
| 17 | 회사채 이자율 | Seibro Playwright (전사 공통) | 동일 | 동일 | ❌ |
| 18 | 카카오맵 위치 | DART `adres` 지오코딩 (더미 주소 차단) | 동일 | 동일 | ❌ |
| 19 | **Global Retain 정보** | UI 외부 링크 (Engagement·ET 조회) | 동일 | 동일 | 동일 |

**우선순위 원칙**: ① DART 정형 API → ② Playwright HTML 폴백 → ③ 외부 정형 API (KRX/KOSIS/국세청/네이버) → ④ UI 수동 입력 / 외부 링크

### A001 감사의견 누락 49건 (≈0.9%) — 자동 보강 18건 완료

DART `accnutAdtorNmNdAdtOpinion.json`이 회사별 응답 스키마 불일치:
- **정상**: 11개 필드(`adt_opinion` 포함) / **누락**: 3개 필드만 반환, `adt_opinion`/`emphs_matter`/`core_adt_matter` 키 자체 없음 + `adtor='-'` placeholder

**자동 보강** (★ 구현 완료): `playwright_f001.py`가 `permanent/{cc}_{YYYY}.json` 스캔 → 모든 연도 빈 의견인 a001 자동 감지 → 사업보고서 본문 직접 파싱. `build_portal.py`가 빈 연도에만 머지(`audit.source:"playwright"`). 현재 18개사 보강 완료(백산·롯데건설·호텔롯데·한섬 등). 16개사는 사업보고서 미제출(SK스퀘어/SK온/스팩 등), 1개사는 보고서 형식 특이로 추후 패턴 보강 예정.

### 캐시 명명 규칙

```
00.회사목록/             company_master_full.json
01.기업개황/             {corp_code}_company_{YYYYMMDD}.json (당일 1파일)
02.재무제표_감사의견/    {corp_code}_financial_{YYYYMMDD}.json + permanent/{corp_code}_{YYYY}.json
03.주가_BPS_PBR/         {corp_code}_stock_{YYYYMMDD}.json
04.대량보유/             {corp_code}_majorstock_{YYYYMMDD}.json
05.뉴스/                 {corp_code}_news_{YYYYMMDD}.json
06.국세청_사업자상태/    {corp_code}_tax_{YYYYMMDD}.json
07.업종통계_KOSIS/       {corp_code}_industry_{YYYYMMDD}.json
08.정기공시보고서/       {corp_code}_reports_{YYYYMMDD}.json
09.주주현황_보고서주석/  {corp_code}_hyslr_{YYYYMMDD}.json
10.세무조사이력/         {corp_code}_taxinv_{YYYYMMDD}.json
11.회사채만기수익률_Seibro/ bond_yields_{YYYYMMDD}.json (전사 공통)
12.감사보고서_Playwright/ {corp_code}_{rcept_no}.json (보고서별 영구)
```

---

### F-00. 로그인 / 인증

| 항목 | 내용 |
|---|---|
| 화면 | 포털 최초 진입 시 모달 |
| 인증 | 이메일 + 사번 → HTML 내장 bcrypt 해시 클라이언트 검증 |
| 세션 | 세션 쿠키 (`expires` 없음, 창 종료 시 자동 만료) |
| 오류 | "이메일 또는 사번이 올바르지 않습니다" (구체적 원인 노출 금지) |
| 비활성화 | `users.xlsx` 활성=Y 사용자만 빌드 포함 |

`users.xlsx` 컬럼: `이메일 / 사번(6자리) / 이름 / 부서 / 활성(Y/N)`. 사번은 빌드 스크립트에서 bcrypt $2a$ 해시로 변환 후 평문 소멸. HTML에는 해시값만 포함.

---

### F-01. 상장 여부 / 내부회계 대상 판단 (외감법 §8)

데이터: DART `company.json` (`corp_cls`: Y=유가증권, K=코스닥, N=코넥스, E=기타). 자산 조회 내성: 최신 보고서 연도 재무제표 부재 시 직전 유효 연도로 자동 폴백. 연결 IC: `ofs_assets or cfs_assets` + `has_cfs` 자동 산출.

| 구분 (직전말 별도재무제표 자산총계) | 결과 |
|---|---|
| 상장사 ≥1,000억 | **감사** |
| 상장사 <1,000억 | **검토** |
| 비상장 일반 ≥5,000억 | **검토** |
| 비상장 특례 (금융업/사보제출/기업집단) ≥1,000억 | **검토** |
| 그 외 | **해당없음** + 담당자 직접 확인 |

> 사업보고서 제출대상·공시대상기업집단 자동 확인 불가 → 담당자 직접 확인 안내 필수.

---

### F-02. 감사의견 및 재무정보

| 항목 | 내용 |
|---|---|
| 데이터 | DART `fnlttSinglAcntAll.json` + `accnutAdtorNmNdAdtOpinion.json` |
| 수집 연도 | 최근 3개 사업연도 (동적: 현재년도 -1/-2/-3) |
| 공시 유형 | a001→A001 / f001→F001 |
| 재무제표 | 연결(CFS) 우선, 별도(OFS) 병행, 전년 ▲▼ 자동 |
| 감사의견 | 3개년 의견·감사인·강조사항·KAM |
| basic/none | 비표시, "DART 보고서 없음" 안내 |
| **A001 49건 빈 의견 보강** | `playwright_f001.py` 사업보고서 본문 직접 파싱 → `cache/12.감사보고서_Playwright/` |

**감사의견 3개년 UI (Brush-stroke)**
- 카드 자체에 색깔띠/배경 없음 (brush-stroke 표준)
- 의견 텍스트: 적정=초록 / 한정·의견거절·부적정=핑크 / 데이터 부재="원문 확인" 회색
- "원문 확인" 케이스: DART 자료는 있음(정형 API 응답 누락) → 사업보고서 직접 링크 (UI 표기 "미공시" 금지)

**재무 요약 대시보드**: 성장성(매출·영업이익·당기순이익 증가율) + 수익성(ROE·ROA·영업이익률·순이익률), 동종업계 벤치마크 비교선 표시. 배지: 강한성장(초록)/안정적성장(파랑)/기준미달(주황)/부진(빨강).

---

### F-03. 신용등급 / 회사채 이자율

UI 수동 입력 (등급기관·등급·등급일·평가일). Seibro 일별 시계열에서 평가일 ↔ 같은 연·월 월말 자료 자동 매칭, 등급별 평균 이자율(1년/3년/5년) + 국고채 3년물 대비 스프레드 자동 계산.

---

### F-04. 동종업계 비교 (KOSIS)

- 데이터: KOSIS OpenAPI (`kosis.api_key`), KSIC 매핑은 DART `induty_code` 앞 2자리 기준
- 표시: 매출성장률·ROE·ROA·영업이익률·순이익률·부채비율 + 배지(업계상위/평균/하위/주의)
- **매칭 우선순위**: ① KSIC 2자리 중분류(37개) → ② 대분류 letter(A~S) → ③ 전산업 평균
- Fallback: 한국은행 기업경영분석 섹터 기본값 (제조 ROE 7% / 정보통신 10% / 금융 9% / 도소매 8% / 건설 9% / 전산업 8%)
- 캐시: 7일 TTL

---

### F-05. 주가·밸류에이션·배당

- **상장 주가**: yfinance(`.KS`) + curl_cffi Chrome TLS(Yahoo bot 우회) → 금융위 API fallback
- **확장 필드**: `target_mean_price`/`analyst_count`/`recommendation_key`/`dividend_yield`/`dividend_rate`/`ex_dividend_date`
- **외국인 지분율**: KRX `MDCSTAT03702` (호출 전 KRX 메인 접근으로 JSESSIONID 선발급)
- **BPS = 별도(OFS) 자본총계 ÷ 발행주식수**. 주식수 우선: `company.isu_shr` → `stockTotqySttus.istc_totqy` → "산출 불가". 연결(CFS) 금지, `majorstock.ctr_stkqy` 금지(개별 보고자 기준).
- **비상장 추정주가 (상증세법 §63)**: 주가=(순자산가치×2 + 순이익가치×3)÷5. 순이익가치=가중평균EPS÷0.10. 가중평균EPS=(당해×3+전년×2+전전년×1)÷6 (3년 미충족 시 2년 3:2).
- **PBR 시각화** (0~3x, 1x 표시): ≥1 초록 "장부가치 초과 — 손상징후 없음" / <1 빨강 "PBR 1 미만 — 손상징후 검토 필요"

---

### F-06. 주주현황

Tier1: DART `hyslrSttus.json` (사업보고서 주석, 최우선) → Tier2: `majorstock.json`(5%↑) → Tier3: `mrhlSttus.json`(상장 소액주주 합계). 합계<100% 시 "기타" 자동 추가. Pie chart + 테이블 병렬.

### F-07. 제재 이력
- 금감원: DART `sanction.json`(Tier1) → `list.json` 제재 키워드 공시 본문 파싱(Tier2)
- 공정위: `case.ftc.go.kr` 법인명 검색 외부 링크

### F-08. 국세청 사업자상태
`POST api.odcloud.kr/api/nts-businessman/v1/status` (b_no 배열은 JSON body). 표시: 계속/휴업/폐업, 과세유형, 폐업일.

### F-09. 뉴스 / 세무조사
- 뉴스: 네이버 검색 API, 회사당 10건
- 세무조사: DART 키워드 공시(세무조사·추징·과세처분·탈세) + 네이버 이중필터 (① 제목에 키워드 ② 제목/요약에 회사명 별칭 — 법인 접미사 제거형·증권코드명 포함)

### F-10~F-12 (간단)
- F-10 법인등기: 인터넷등기소 공개 API 부재 → 외부 검색 링크
- F-11 카카오맵: DART `adres` + Kakao SDK (JS 앱키 HTML 노출 불가피, 240px 마커, 실패 시 외부 링크)
- F-12 헤더 날씨: `wttr.in/Seoul?format=j1` (인증 불필요, CORS 허용)

---

## 5. 빌드·배포

```bash
cd 3.포털빌드

python collect_all.py                                     # 수집 + 빌드 + 배포
python 1.수집/collect.py [--test 50] [--skip-bond]        # 수집만 (전체)
python 1.수집/collect.py --from STEP                      # 특정 단계부터 재시작
# STEP: master / company / financial / shareholder / stock / news /
#       tax-status / kosis / reports / shareholder-full / tax-investigation
python 2.빌드/build_portal.py --skip-upload               # 빌드만
python 3.운영/deploy_git.py                               # 배포만 (git push)
```

빌드 결과: `output/index.html` (단일 파일, 데이터 내장). 배포: `JSKIMKorea/FirmView` GitHub Pages.

---

## 5-A. 디자인 시스템 (톤다운 + Brush-stroke 표준)

PwC 브랜드 팔레트 외 색상 추가 금지. 흰색·옅은 회색 베이스 + 절제된 오렌지 액센트.

**핵심 룰**:
- **헤더 띠**: `#FE7C39` (Orange 400, 한 단계 연함) + 텍스트 검정 `#000000` (가독성)
- CSS 시멘틱 변수: `--green`=#2E7D32 / `--red`=#C62828 / `--bg-elev`=옅은 회색
- 강한 오렌지 그라데이션 금지 (IC/세무/TI 결과 카드 옅은 단색으로)
- **그래프 색 통일**: 음수일 때도 `var(--pwc-orange)` (방향은 ▲▼·텍스트로 구분)
- **모든 숫자 천단위 콤마 필수**: `toLocaleString('ko-KR')` 또는 `fmtNum(n)` 사용
- 더미 주소(`xx`/`xxx`/`예시`/`sample`) 카카오맵 SDK 호출 차단 → 안내 placeholder만

**Brush-stroke 강조** (라벨·배지):
```css
.elem{position:relative;isolation:isolate;padding:2px 14px 2px 4px}
.elem::before{content:'';position:absolute;inset:0;
  background:linear-gradient(108deg,rgba(R,G,B,.18) 0%,rgba(R,G,B,.08) 55%,transparent 100%);
  z-index:-1;transform:skewX(-5deg);border-radius:3px}
```
색: 적정·동종업계=초록(34,160,70) / 한정·의견거절=핑크(210,50,70) / 별도·연결·현재가=오렌지(253,81,8) / 주가 메트릭=보라(100,80,200) / IC·재무 헤드=인디고(59,130,246)

**금지/예외**: 장식용 colored border-left/border-top 신규 추가 금지 (TOC active만 예외) / 로고 배너 흰 박스 금지 / UI 표기 "미공시" 금지(→"원문 확인"). 회색 구조용 divider(`var(--border)`) 유지

---

## 6. 비기능 요구사항

| 항목 | 기준 |
|---|---|
| HTML 크기 | 7,344개 기업 기준 수십 MB (GitHub Pages 100MB 제한) |
| 브라우저 | Chrome/Edge 최신 |
| 인증 만료 | 창 닫으면 재로그인 (세션 쿠키, expires 없음) |
| 수집 시간 | 전체 약 6~10시간 (캐시로 중단·재실행) |

---

## 7. API 키 현황

| API | 키 상태 | 비고 |
|---|---|---|
| DART OpenAPI | ✅ | `dart.api_keys` 6개 키 순환 |
| KOSIS / 국세청 / 네이버 / 카카오 / GitHub | ✅ | 모두 무료, `credentials.json` 관리 |
| wttr.in | 불필요 | 공개 무료 |

---

## 8. 화면 구성

**헤더**: 포털명 | 사용자/부서 | 날씨 위젯 | 로그아웃 / **검색창** (자동완성)

**사이드바 4개 그룹 / 카드 14개**:
- **Company Overview** ① 기업 기본정보(카카오맵 240px) ② 내부회계관리제도 ③ 감사의견(3개년)
- **Financial Analysis** ④ 재무제표(연결/별도) ⑤ 재무 요약 ⑥ 동종업계 비교(KOSIS) ⑦ 신용등급+회사채 이자율 ⑧ 주가·BPS·PBR+배당 ⑨ 주주현황
- **Compliance & Legal** ⑩ 제재이력 ⑪ 국세청 사업자상태 ⑫ 세무조사 이력 ⑬ 최신 뉴스 ⑭ 법원 등기정보
- **PwC Reference** ⑮ Global Retain 정보 (외부 링크 카드 — Engagement·ET 정보는 https://jskimkorea.github.io/retain-viewer/ 에서 조회)
