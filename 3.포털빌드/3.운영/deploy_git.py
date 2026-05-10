"""
deploy_git.py - git push 방식 배포 (대용량 파일 대응)

GitHub Contents API 한계(~50MB) 초과 시 사용.
output/index.html을 임시 클론에 복사 후 git push로 배포.

사용법:
  python 3.운영/deploy_git.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT        = Path(__file__).parent.parent.parent   # 종합기업정보/
PORTAL_ROOT = Path(__file__).parent.parent          # 3.포털빌드/
OUTPUT_FILE = PORTAL_ROOT / "output" / "index.html"


def run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def pause_and_exit(code: int = 0) -> None:
    print("\n" + "=" * 60)
    if code == 0:
        print("  완료. 창을 닫으려면 Enter 키를 누르세요.")
    else:
        print("  오류로 종료됨. 내용 확인 후 Enter 키를 누르세요.")
    print("=" * 60)
    try:
        input()
    except Exception:
        pass
    sys.exit(code)


def main() -> None:
    cred_path = ROOT / "credentials.json"
    if not cred_path.exists():
        print(f"[오류] credentials.json 없음: {cred_path}")
        pause_and_exit(1)

    cred = json.loads(cred_path.read_text(encoding="utf-8"))
    gh     = cred.get("github", {})
    token  = gh.get("token", "")
    repo   = gh.get("repo", "")           # JSKIMKorea/FirmView
    branch = gh.get("branch", "main")
    path   = gh.get("path", "index.html") # 리포 내 경로

    if not token or not repo:
        print("[오류] credentials.json → github.token / github.repo 미설정")
        pause_and_exit(1)

    if not OUTPUT_FILE.exists():
        print(f"[오류] 빌드 파일 없음: {OUTPUT_FILE}")
        print("  먼저 빌드: python 2.빌드/build_portal.py --skip-upload")
        pause_and_exit(1)

    size_mb = OUTPUT_FILE.stat().st_size / 1024 / 1024
    print(f"output/index.html 로드 ({size_mb:.2f} MB)")

    clone_url = f"https://{token}@github.com/{repo}.git"
    portal_url = f"https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/"

    tmpdir = tempfile.mkdtemp(prefix="firmview_deploy_")
    print(f"임시 폴더: {tmpdir}")

    try:
        # 1. shallow clone (최신 커밋만)
        print(f"클론 중 ({repo}) ...")
        rc, out, err = run_git(["clone", "--depth=1", "--branch", branch, clone_url, tmpdir], cwd=tmpdir)
        if rc != 0:
            print(f"[오류] git clone 실패:\n{err}")
            pause_and_exit(1)

        # 2. index.html 복사
        dest = Path(tmpdir) / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OUTPUT_FILE, dest)
        print(f"파일 복사 완료: {path} ({size_mb:.2f} MB)")

        # 3. git config (커밋 메타)
        run_git(["config", "user.email", "deploy@firmview.local"], cwd=tmpdir)
        run_git(["config", "user.name",  "FirmView Deploy"], cwd=tmpdir)

        # 4. add + commit
        run_git(["add", path], cwd=tmpdir)
        msg = f"포털 배포 ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
        rc, out, err = run_git(["commit", "-m", msg], cwd=tmpdir)
        if rc != 0:
            if "nothing to commit" in out + err:
                print("변경 사항 없음 — 이미 최신 상태입니다.")
                pause_and_exit(0)
            print(f"[오류] git commit 실패:\n{out}\n{err}")
            pause_and_exit(1)

        # 5. push
        print("GitHub push 중 ...")
        rc, out, err = run_git(["push", "origin", branch], cwd=tmpdir)
        if rc != 0:
            print(f"[오류] git push 실패:\n{err}")
            pause_and_exit(1)

        print(f"\nGitHub Pages 배포 완료 → {portal_url}")
        print("  (GitHub Actions 빌드 완료까지 약 1~2분 소요)")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    pause_and_exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[중단] 사용자가 Ctrl+C로 중단했습니다.")
        pause_and_exit(1)
    except Exception:
        print("\n" + "=" * 60)
        print("  [예상치 못한 오류 발생]")
        print("=" * 60)
        traceback.print_exc()
        pause_and_exit(1)
