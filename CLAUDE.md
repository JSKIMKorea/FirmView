# CLAUDE.md — 종합기업정보 Portal 개발 규칙

## Git 커밋 규칙 (★ 핵심)
- **작업 단위마다 즉시 커밋** (모아서 X)
- **파일 단위 add만** (`git add -A` 금지 — credentials.json·users.xlsx 유출 방지)
- 커밋 메시지: `[YYYY-MM-DD HH:MM] 짧은 요약 — 상세` + 변경 bullet + Co-Authored-By
- 롤백: `git stash → git reset --hard HEAD~N` / 실수 reset은 `git reflog`

---

## 프로젝트 컨텍스트
- **명칭**: 종합기업정보 Portal ("포털" 표기 금지)
- **목적**: PwC 내부 감사·자문팀 기업정보 통합 조회
- **배포**: GitHub Pages — https://jskimkorea.github.io/FirmView/
- **아키텍처**: Python 수집 → 단일 HTML 빌드 → GitHub Pages 정적 호스팅
- **인증**: PwC 이메일 + 사번 6자리, bcrypt 해시, 세션 쿠키 (브라우저 종료 시 자동 만료)

---

## 수집 대상 모집단 (총 7,344개 — A001 ∪ Retain Viewer)

| dart_data_level | 합계 | Retain | 비Retain | 수집 범위 |
|---|---|---|---|---|
| **a001** | 3,476 | 1,096 | 2,380 | 재무·감사의견·주주·공시·주가 풀데이터 |
| **f001** | 2,414 | 2,414 | 0 | Playwright 감사보고서 HTML 파싱 |
| **basic** | 786 | 786 | 0 | 기업개황·뉴스·국세청·KOSIS만 |
| **none** | 668 | 668 | 0 | 검색 자동완성만 (수집 스킵) |

> 카테고리×유형 추출 매트릭스는 [README.md "기업 유형별 데이터 추출 매트릭스"](README.md) 참조.

---

## 기술 스택 (고정 — 변경 금지)
- **프론트**: Vanilla HTML/CSS/JS (React/Vue 금지)
- **차트**: Chart.js 인라인 / **지도**: Kakao Maps SDK (다른 지도 SDK 금지)
- **폰트**: `'Noto Sans KR'` 단일 — `*` 셀렉터 강제, 다른 폰트 절대 금지
- **수집**: Python `httpx` 비동기 (`dart_master.py`만 `requests` 동기 — 회사망 프록시 자동 인식)
- **인증 빌드**: `bcrypt` / 런타임: `bcryptjs` 인라인. UMD `dcodeIO.bcrypt` → `window.bcrypt` 별칭 필수

---

## PwC 디자인 팔레트
```css
--pwc-orange:#FD5108  --pwc-orange-deep:#C44608
--pwc-orange-400:#FE7C39  -300:#FFAA72  -200:#FFCDA8  -100:#FFE8D4  -50:#FFF5ED
--pwc-grey 500..50: A1A8B3·B5BCC4·CBD1D6·DFE3E6·EEEFF1·F5F7F8
```
- 본문 텍스트: `#000000` (라이트) / `#FFFFFF` (다크). `--pwc-grey-500` 본문 사용 금지
- 비-브랜드 색 추가 금지 (단, 의미색 — 초록 #2E7D32 / 빨강 #C62828 / 인디고 #3B82F6 등은 의미별 유지)
- **헤더 띠**: 라이트 `#FE7C39` (Orange 400, 한 단계 연함) + 텍스트 `#000000` (검정 가독성)
- 다크 헤더: `#1A1A1A`

---

## 파일 경로 (3.포털빌드/)
- `1.수집/00.회사목록/dart_master.py` — A001 ∪ Retain Viewer 유니버스
- `1.수집/01.DART_공시/` — `dart_key_manager.py`(6키 순환) / `company.py`(개황) / `financial.py`(재무·감사) / `shareholder.py`(5%↑) / `shareholder_full.py`(주주 주석 a001) / `reports.py`(정기공시) / `playwright_f001.py`(F001 + a001 빈 의견 자동 보강)
- `1.수집/02.주가/stock.py` — yfinance + 금융위 + 상증세법 + KRX 외국인 / `03.뉴스/news.py` / `04.세무/tax_status.py·tax_investigation.py` / `05.KOSIS_업종통계/industry.py` / `06.Seibro_회사채이자율/bond_yields.py`
- `1.수집/collect.py` — 전체 수집 (master부터). `--from STEP`으로 특정 단계부터 재시작
- `2.빌드/build_portal.py` / `3.운영/update_users.py·deploy_git.py` / `templates/portal.html` / `cache/`(gitignored, 12개 하위) / `output/index.html`(gitignored)
- 외부: `2.사용자관리/users.xlsx`(Git 금지·사번 평문) / `1.회사명정리/output/company_master.json`(read-only) / `credentials.json`(Git 금지)
- HTML 플레이스홀더: `__PORTAL_DATA_JSON__`, `__PORTAL_USERS_JSON__`, `__KAKAO_JS_KEY__`, `__BUILD_TIME__`, `/*__BCRYPT_JS__*/`, `/*__CHART_JS__*/`, `__GH_LOG_TOKEN_CODES__`, `__GH_LOG_REPO__`

---

## 인증 — 세션 쿠키
- `expires`/`max-age` 없이 쿠키 → 브라우저 종료 시 자동 만료
- 페이지 로드 시 PORTAL_USERS에 이메일 존재 여부 재검증 (퇴사 안전장치)
- 런타임: `bcrypt.compareSync(사번, user.hash)`
- `users.xlsx`: `azure_auto`(Azure SQL) + `manual_add`(수기) 2시트, 이메일 중복 시 manual 우선

### 로그인 로그 — GitHub Private (★)
- 로그인 성공 직후 `logLogin(info)` → Private `log.csv` 1행 prepend (CSV: `로그인일시,이메일,이름,본부,사번`, UTF-8 BOM, KST `YYYY-MM-DD HH:MM:SS KST`)
- **토큰은 charcode 배열만** (`atob`/평문 절대 금지 — Secret Scanning Push Protection 차단). credentials.json: `gh_log:{token,repo}`. 빈 토큰이면 logLogin 즉시 return
- 토큰 권한: Fine-grained PAT, 대상 저장소 1개, Contents R/W만. log 저장소 Settings → Push protection → **Disabled**
- Fire-and-forget(`.catch(()=>{})`). 한글: `decodeURIComponent(escape(atob()))` / `btoa(unescape(encodeURIComponent()))`. BOM 0xFEFF 보존

---

## 비즈니스 로직

### 내부회계관리제도 (외감법 §8) — 별도재무제표 자산총계 기준
| 자산 | 결과 |
|---|---|
| 상장 ≥1,000억 | 감사 / 상장 <1,000억 | 검토 |
| 비상장 금융(KSIC 64/65/66) ≥1,000억 | 검토 |
| 비상장 일반 ≥5,000억 | 검토 / 그 외 | 해당없음 + 담당자 직접확인 |

자산: 최신 OFS 우선 → 없으면 이전 연도 폴백. 연결 IC: `ofs_assets or cfs_assets` + `has_cfs`.

### BPS·PBR
- `BPS = 별도 자본총계 ÷ 발행주식수`. 주식수: `company.isu_shr` → `stockTotqySttus.istc_totqy`
- `majorstock.ctr_stkqy` 사용 금지 (개별 보고자 기준수)
- BPS > 1억원 → 데이터 오류 (`bps:null`). null은 `--`/"산출 불가" 표시 (0 대체 금지)

### 비상장사 추정주가 (상증세법 §63)
- 주가 = (순자산가치×2 + 순이익가치×3) ÷ 5
- 순이익가치 = 가중평균EPS÷0.10 / EPS = (당해×3+전년×2+전전년×1)÷6 (3년 미충족 시 2년 3:2)

### KRX 외국인 지분율
- `data.krx.co.kr` `MDCSTAT03702`, isuCd `KR7{종목코드}000`. 호출 전 KRX 메인 접근으로 JSESSIONID 선발급 필수

### 감사의견 — A001 빈 의견 자동 보강 (★)
- DART `accnutAdtorNmNdAdtOpinion.json` 응답 스키마 회사별 불일치 → 49개 a001(약 0.9%)에서 `adt_opinion` 필드 누락
- **자동 보강**: `playwright_f001.py`가 `permanent/{cc}_{YYYY}.json` 스캔 → 모든 연도 빈 의견인 a001을 자동 감지 → 사업보고서 본문 직접 파싱 → `cache/12.감사보고서_Playwright/{cc}_{rcept_no}.json`
- `build_portal.py`가 a001 financials의 빈 의견 연도에 PW 데이터 머지 (이미 의견 있는 연도는 건드리지 않음, `audit.source:"playwright"` 표시)
- 보강 실패 시 UI 배지 "원문 확인" + DART 사업보고서 직접 링크 (UI "미공시" 표기 금지 — DART에 자료는 있음)

### 주주현황·세무조사·동종업계
- 주주: `hyslrSttus.json` 메인(주석) > `majorstock.json`(5%↑). 합계<100% 시 "기타" 자동 추가
- 세무조사: 제목에 키워드(세무조사·추징·과세처분·국세청 조사·탈세) + 제목/요약에 회사명 별칭(법인 접미사 제거형 포함). build_portal.py가 캐시 소급 적용
- 동종업계: KSIC 2자리(37개) → 대분류 letter → 전산업 평균

---

## UI 규칙

### 카드 순서 (14개) + 사이드바 그룹 (4개)
1. **Company Overview**: 기업 기본정보(카카오맵 240px) · 내부회계 · 감사의견(3년)
2. **Financial Analysis**: 재무제표(연결/별도) · 재무 요약 · 동종업계 · 신용등급+회사채 이자율 · 주가·BPS·PBR · 주주현황
3. **Compliance & Legal**: 제재이력 · 국세청 · 세무조사 · 뉴스 · 법원등기
4. **PwC Reference**: Global Retain 정보 (외부 링크 카드, https://jskimkorea.github.io/retain-viewer/)

### 레이아웃·표현
- 각 카드 우측 `row-side` 안내 박스 (2:1 그리드, 1280px 이하 1단)
- 날짜: `YYYY.MM.DD` (`fmtYMD()` 헬퍼)
- UI 금지어: "API", "직링크", "fallback", DART 내부 식별자 노출
- 다크모드: `localStorage.portal_theme`, 헤더 iOS 스타일 토글
- 사이드바 `.toc-group`은 `text-transform:uppercase` 강제 → "PwC" 등 대소문자 보존이 필요하면 `style="text-transform:none"` 예외

### 그래프 색 통일 (★)
- 주요 재무 요약 / 동종업계 비교 그래프: 음수일 때도 동일 `var(--pwc-orange)` (deep orange 분기 금지). 방향 정보는 ▲▼ 화살표·텍스트로만 구분

### Brush-stroke 효과 (강조 표준)
강조 텍스트(라벨·배지)는 박스/colored border 대신 `::before` brush:
```css
.elem{position:relative;isolation:isolate;padding:2px 14px 2px 4px}
.elem::before{content:'';position:absolute;inset:0;
  background:linear-gradient(108deg,rgba(R,G,B,.18) 0%,rgba(R,G,B,.08) 55%,transparent 100%);
  z-index:-1;transform:skewX(-5deg);border-radius:3px}
```
색: 적정·동종업계=초록 / 한정·의견거절=핑크 / 별도·연결·현재가=오렌지 / 주가 라벨=보라 / IC·재무 헤드=인디고

### 색깔띠 금지 / 로고 배너 / 예시 화면 카카오맵
- 장식용 colored border-left/border-top 신규 추가 금지 (회색/`var(--border)` 외). TOC active만 예외
- 로고 배너 (`.ls-logo`/`.ls-grv-banner`): 흰 배경+border 박스 금지. 72×44 + 옅은 오렌지 brush. img는 `width:100%;height:100%;object-fit:contain`
- `renderMap()`은 `xx`/`xxx`/`예시`/`sample` 시작 더미 주소면 SDK 호출 차단 → 안내 placeholder만

### 톤다운 (주황 절제)
- 흰색·옅은 회색 베이스. 카드 배경 그라데이션은 흰색/`var(--bg-elev)`(=옅은 회색) 위주
- 텍스트는 검정/회색 기본, 오렌지(`pwc-orange-deep`)는 핵심 강조에만
- IC/세무/TI 결과 카드: 강한 오렌지 그라데이션 금지 → 옅은 단색
- credit-form 인풋 border: 회색 (오렌지 금지)
- CSS 변수 시멘틱: `--green`=#2E7D32 (양호) / `--red`=#C62828 (위험) / `--bg-elev`=#F5F7F8 (옅은 회색)

### 숫자 표기
- **모든 숫자는 천단위 콤마 필수**. JS: `toLocaleString('ko-KR')` 또는 `fmtNum(n)`. `toFixed(0)`만 쓰면 콤마 누락
- 단위 한글 변환(억/조/만)도 `fmtKMarketCap` 헬퍼 사용. 예: `2531억 원` ❌ → `2,531억 원` ✅

---

## 회사망·네트워크 (★ 핵심)
- `dart_master.py`: `requests` 동기 + `verify=False` (**`httpx` 절대 금지** — `corpCode.xml` ZIP 단일 다운로드. `list.json` 반복 호출 시 ConnectionReset 10054)
- 나머지 수집: `httpx.AsyncClient(verify=False)`
- `collect.py`: Windows 레지스트리 프록시 → `HTTP_PROXY`/`HTTPS_PROXY` 환경변수
- opendart.fss.or.kr 차단 시: VPN 한국→미국 전환

## DART API 키 순환 (★ 핵심)
- `1.수집/01.DART_공시/dart_key_manager.py` / `credentials.json → dart.api_keys` 6개
- 키 #1부터 → status `"020"`(한도) 즉시 다음 키 → 소진 시 중단
- 회전 트리거: `"020"`/`"010"`(미등록)/`"011"`(사용불가). `"013"`(없음)은 회전 안 함

---

## 빌드·배포 명령
```bash
cd 3.포털빌드
python 1.수집/collect.py [--test 50]                   # 전체 수집 (6~10h, master부터)
python 1.수집/collect.py --from financial              # 특정 단계부터 재시작
python 2.빌드/build_portal.py --skip-upload            # 빌드만
python 3.운영/deploy_git.py                            # GitHub Pages 배포 (git push)
python collect_all.py [--test N] [--build-only]        # 전체 자동 (수집+빌드+배포)
```
**`--from` STEP**: `master`/`company`/`financial`/`shareholder`/`stock`/`news`/`tax-status`/`kosis`/`reports`/`shareholder-full`/`tax-investigation`

> 배포는 `deploy_git.py`(git push, 62MB 초과 대응)만 사용. GitHub Contents API 방식은 deprecated.

---

## 구현 금지 사항
- DART/KOSIS/국세청/네이버 API 키 HTML 포함
- 카카오맵 외 지도 SDK / `'Noto Sans KR'` 외 폰트 / PwC 팔레트 외 색상
- 감사의견·내부회계 단정적 문구 ("해야 한다", "위반이다")
- 사번 평문 저장·비교 / 로그인 성공·실패 이유 구분 메시지 / null 지표 0 대체
- `1.회사명정리/` 수정(원본) / `users.xlsx`·`credentials.json` Git 업로드
- 장식용 colored border-left/border-top 신규 추가 (TOC active만 예외)
- 로고 배너에 흰 배경 박스 / "미공시" UI 표기 (DART 자료 있음 — "원문 확인"으로)
- 그래프 음수 분기 다른 색 (모두 `var(--pwc-orange)`로 통일)
- 더미 주소 카카오맵 SDK 호출 (`renderMap` 진입 시 차단)
