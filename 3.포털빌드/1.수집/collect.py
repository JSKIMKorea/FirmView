"""
collect.py - 수집 전용 실행기 (빌드·배포 없음)

A001 ∪ Retain Viewer 전체 기업을 기준으로 수집.
캐시 폴더에 당일 파일이 있으면 자동 스킵.
중간에 끊겨도 재실행하면 이어서 진행됨.

사용법:
  python collect.py                          # 전체 수집 (~7,344개, master부터)
  python collect.py --test 15                # 레벨별 15개씩 (a001/f001/basic/none 각 15개)
  python collect.py --skip-bond              # Seibro 채권 추출 스킵
  python collect.py --skip-playwright        # F001 Playwright 스킵
  python collect.py --from financial         # 재무부터 재시작 (master·company 스킵)
  python collect.py --from kosis             # KOSIS부터 재시작
  python collect.py --from shareholder-full  # 주주현황(주석)부터 재시작

--from 단계 목록 (이 단계 포함 이후부터 실행):
  master | company | financial | shareholder | stock | news |
  tax-status | kosis | reports | shareholder-full | tax-investigation
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE        = Path(__file__).parent           # 1.수집/
PORTAL_ROOT = HERE.parent                     # 3.포털빌드/
FULL_MASTER = PORTAL_ROOT / "cache" / "00.회사목록" / "company_master_full.json"


# 단계 순서 (--from 옵션에서 사용)
_STEP_ORDER = [
    "master",            # 0단계: dart_master.py (회사 유니버스 생성)
    "company",           # 1단계: 기업개황
    "financial",         # 2단계: 재무·감사의견
    "shareholder",       # 3단계: 대량보유 5%↑
    "stock",             # 4단계: 주가/BPS/PBR
    "news",              # 5단계: 뉴스
    "tax-status",        # 6단계: 국세청 사업자상태
    "kosis",             # 7단계: KOSIS 업종통계
    "reports",           # 8단계: 정기공시 보고서
    "shareholder-full",  # 9단계: 주주현황 주석
    "tax-investigation", # 10단계: 세무조사 흔적
]


def _setup_windows_proxy() -> None:
    """Windows 시스템 프록시를 환경변수로 등록 - 하위 httpx 스크립트가 자동 사용"""
    if "HTTPS_PROXY" in os.environ or "HTTP_PROXY" in os.environ:
        return
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if not enabled:
                return
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
            if "=" in server:
                mapping = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
                server = mapping.get("https", mapping.get("http", server))
            if not server.startswith("http"):
                server = f"http://{server}"
            os.environ["HTTP_PROXY"]  = server
            os.environ["HTTPS_PROXY"] = server
            print(f"  시스템 프록시 감지: {server}")
    except Exception:
        pass


def _make_stratified_test_master(full_master_path: Path, per_level: int) -> Path:
    """dart_data_level 별로 per_level개씩 추출한 테스트 마스터 파일 생성"""
    data = json.loads(full_master_path.read_text(encoding="utf-8"))
    companies = data.get("companies", data) if isinstance(data, dict) else data

    subset, counts = [], {}
    for level in ("a001", "f001", "basic", "none"):
        bucket = [c for c in companies if c.get("dart_data_level") == level]
        selected = bucket[:per_level]
        subset.extend(selected)
        counts[level] = len(selected)

    print(f"\n  [테스트 층화 추출]")
    for lv, n in counts.items():
        print(f"    {lv:5s}: {n}개")
    print(f"    합계: {len(subset)}개")

    out_path = full_master_path.parent / "company_master_test.json"
    out_data = {
        **{k: v for k, v in data.items() if k != "companies"},
        "companies":   subset,
        "total":       len(subset),
        "test_mode":   True,
        "level_counts": counts,
    }
    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → 테스트 마스터 저장: {out_path}\n")
    return out_path


def pause_and_exit(code: int = 0) -> None:
    print("\n" + "="*60)
    if code == 0:
        print("  완료. 창을 닫으려면 Enter 키를 누르세요.")
    else:
        print("  오류로 종료됨. 내용 확인 후 Enter 키를 누르세요.")
    print("="*60)
    try:
        input()
    except Exception:
        pass
    sys.exit(code)


def run(script: Path, label: str = "", extra_env: dict | None = None) -> None:
    cmd = [sys.executable, str(script)]
    env = {**os.environ, **(extra_env or {})}
    start = time.time()
    print(f"\n{'='*60}")
    print(f"  {label or script.name}")
    print(f"{'='*60}")

    if not script.exists():
        print(f"\n[오류] 스크립트 파일을 찾을 수 없습니다: {script}")
        pause_and_exit(1)

    result = subprocess.run(cmd, cwd=str(PORTAL_ROOT), env=env)
    elapsed = time.time() - start
    if result.returncode != 0:
        print(f"\n[오류] {script.name} 실패 (종료코드 {result.returncode})")
        pause_and_exit(1)
    print(f"  → 완료 ({elapsed:.1f}초)")


def main() -> None:
    parser = argparse.ArgumentParser(description="종합기업정보 Portal - 수집 실행기")
    parser.add_argument("--test", type=int, default=0, metavar="N",
                        help="레벨별 N개씩 층화 추출 (a001/f001/basic/none 각 N개)")
    parser.add_argument("--skip-bond", action="store_true",
                        help="Seibro 채권 추출 스킵 (Playwright 미설치 환경)")
    parser.add_argument("--skip-playwright", action="store_true",
                        help="F001 감사보고서 Playwright 스크래핑 스킵 (분산 실행 시)")
    parser.add_argument("--from", dest="from_step", default="master",
                        choices=_STEP_ORDER, metavar="STEP",
                        help=f"이 단계부터 실행 (이전 단계 스킵). 기본 master. 선택: {', '.join(_STEP_ORDER)}")
    args = parser.parse_args()

    per_level = args.test
    from_idx  = _STEP_ORDER.index(args.from_step)
    total_start = time.time()

    _setup_windows_proxy()

    print(f"\n{'='*60}")
    print("  종합기업정보 Portal - 데이터 수집")
    print(f"  시작:    {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  스크립트: {Path(__file__)}")
    print(f"  작업폴더: {PORTAL_ROOT}")
    if args.from_step != "master":
        print(f"  시작단계: {args.from_step} (이전 단계 스킵)")
    if per_level:
        print(f"  [테스트] 레벨별 {per_level}개씩 (최대 {per_level*4}개)")
    print(f"{'='*60}")

    cred_path = PORTAL_ROOT.parent / "credentials.json"
    if not cred_path.exists():
        print(f"\n[오류] credentials.json 없음: {cred_path}")
        pause_and_exit(1)

    def _skip(step: str) -> bool:
        return _STEP_ORDER.index(step) < from_idx

    # 0단계: dart_master.py
    if _skip("master"):
        if not FULL_MASTER.exists():
            print(f"\n[오류] master 스킵했지만 회사목록 없음: {FULL_MASTER}")
            print("  --from master 또는 --from 옵션 없이 실행하세요.")
            pause_and_exit(1)
        print(f"\n  [0단계 master 스킵] 기존 마스터 사용: {FULL_MASTER}")
    else:
        dart_master = HERE / "00.회사목록" / "dart_master.py"
        if not dart_master.exists():
            print(f"\n[오류] dart_master.py 없음: {dart_master}")
            pause_and_exit(1)
        print(f"\n{'='*60}")
        print("  0/10  DART A001 전체 기업 목록 생성")
        print(f"{'='*60}")
        result = subprocess.run([sys.executable, str(dart_master)], cwd=str(PORTAL_ROOT))
        if result.returncode != 0:
            print(f"\n[오류] dart_master.py 실패 (종료코드 {result.returncode})")
            pause_and_exit(1)
        if not FULL_MASTER.exists():
            print(f"\n[오류] 기업 목록 파일 미생성: {FULL_MASTER}")
            pause_and_exit(1)

    # 테스트 모드: 레벨별 per_level개씩 층화 추출
    if per_level:
        master = _make_stratified_test_master(FULL_MASTER, per_level)
    else:
        master = FULL_MASTER
    extra_env = {"PORTAL_MASTER": str(master)}
    print(f"  → 마스터: {master}")

    # 1~10단계
    steps = [
        ("company",           HERE / "01.DART_공시"      / "company.py",          "1/10  기업개황 (DART)"),
        ("financial",         HERE / "01.DART_공시"      / "financial.py",        "2/10  재무정보 + 감사의견 (DART)"),
        ("shareholder",       HERE / "01.DART_공시"      / "shareholder.py",      "3/10  대량보유 5%↑ (DART)"),
        ("stock",             HERE / "02.주가"           / "stock.py",            "4/10  주가/BPS/PBR"),
        ("news",              HERE / "03.뉴스"           / "news.py",             "5/10  뉴스 (네이버)"),
        ("tax-status",        HERE / "04.세무"           / "tax_status.py",       "6/10  국세청 사업자상태"),
        ("kosis",             HERE / "05.KOSIS_업종통계" / "industry.py",         "7/10  업종통계 (KOSIS)"),
        ("reports",           HERE / "01.DART_공시"      / "reports.py",          "8/11  정기 공시 보고서 (DART)"),
        ("shareholder-full",  HERE / "01.DART_공시"      / "shareholder_full.py", "9/11  주주현황 주석 (DART A001)"),
        ("tax-investigation", HERE / "04.세무"           / "tax_investigation.py","10/11 세무조사 흔적"),
    ]
    for step, script, label in steps:
        if _skip(step):
            print(f"\n  [{label} 스킵]")
        else:
            run(script, label, extra_env)

    # 추가 단계 — F001 Playwright + Seibro
    if not args.skip_playwright:
        run(HERE / "01.DART_공시" / "playwright_f001.py",
            "11/11 F001 감사보고서 HTML 스크래핑 (Playwright)", extra_env)
    else:
        print("\n  [F001 Playwright 스킵] --skip-playwright 옵션 적용")
        print("  → 다른 컴퓨터에서 playwright_f001.py 실행 후 cache/12.감사보고서_Playwright/ 복사")

    if not args.skip_bond:
        run(HERE / "06.Seibro_회사채이자율" / "bond_yields.py",
            "추가  Seibro 무보증회사채 이자율 (Playwright)", extra_env)

    elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"  수집 완료 ({elapsed/60:.1f}분)")
    print(f"  다음: python 2.빌드/build_portal.py --skip-upload  → python 3.운영/deploy_git.py")
    print(f"{'='*60}")
    pause_and_exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[중단] 사용자가 Ctrl+C로 중단했습니다.")
        pause_and_exit(1)
    except Exception:
        print("\n" + "="*60)
        print("  [예상치 못한 오류 발생]")
        print("="*60)
        traceback.print_exc()
        pause_and_exit(1)
