"""
collect_all.py - 전체 실행 (수집 + 빌드 + 배포)

DART A001 사업보고서 제출 기업 전체를 기준으로 수집 후 빌드·배포.

단계별 실행:
  수집만:   python 1.수집/collect.py
  빌드만:   python 2.빌드/build_portal.py --skip-upload
  배포만:   python 3.운영/deploy_git.py

전체 한번에:
  python collect_all.py                # 수집 + 빌드 + 배포
  python collect_all.py --test 50      # 50개 회사로 테스트
  python collect_all.py --build-only   # 수집 스킵, 빌드 + 배포만
  python collect_all.py --skip-bond    # Seibro 채권 추출 스킵
"""
import argparse
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent   # 3.포털빌드/

COLLECT_SCRIPT = HERE / "1.수집"  / "collect.py"
BUILD_SCRIPT   = HERE / "2.빌드"  / "build_portal.py"
DEPLOY_SCRIPT  = HERE / "3.운영"  / "deploy_git.py"


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


def run(script: Path, *extra_args: str) -> None:
    if not script.exists():
        print(f"\n[오류] 스크립트 파일을 찾을 수 없습니다: {script}")
        pause_and_exit(1)
    result = subprocess.run(
        [sys.executable, str(script), *extra_args],
        cwd=str(HERE),
    )
    if result.returncode != 0:
        print(f"\n[오류] {script.name} 실패 (종료코드 {result.returncode})")
        pause_and_exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="종합기업정보 Portal - 전체 실행")
    parser.add_argument("--test", type=int, default=0, metavar="N",
                        help="N개 회사로만 수집 (테스트용)")
    parser.add_argument("--build-only", action="store_true",
                        help="수집 스킵, 빌드 + 배포만 실행")
    parser.add_argument("--skip-bond", action="store_true",
                        help="Seibro 채권 추출 스킵 (Playwright 미설치 환경)")
    args = parser.parse_args()

    total_start = time.time()

    print(f"\n{'='*60}")
    print("  종합기업정보 Portal - 전체 실행")
    print(f"  시작: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if args.test:
        print(f"  [테스트 모드] 최대 {args.test}개 회사")
    if args.build_only:
        print("  [빌드 전용] 수집 스킵")
    print(f"{'='*60}")

    # ── 1단계: 수집 ──────────────────────────────────────────
    if not args.build_only:
        collect_args = []
        if args.test:
            collect_args += ["--test", str(args.test)]
        if args.skip_bond:
            collect_args.append("--skip-bond")
        run(COLLECT_SCRIPT, *collect_args)

    # ── 2단계: 빌드 (build_portal.py --skip-upload) ──────────
    run(BUILD_SCRIPT, "--skip-upload")

    # ── 3단계: 배포 (deploy_git.py - git push 방식, 62MB 초과 대응) ──
    run(DEPLOY_SCRIPT)

    elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"  전체 완료 ({elapsed/60:.1f}분)")
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
