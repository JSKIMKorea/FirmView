"""
update_users.py -- 사용자 목록 갱신 (Azure SQL → users.xlsx)

Azure SQL의 BI_STAFFREPORT_EMP_V를 조회하여
2.사용자관리/users.xlsx 파일의 [azure_auto] 시트만 갱신합니다.
[manual_add] 시트는 수기 관리용으로, 이 스크립트가 절대 변경하지 않습니다.

build_portal.py 실행 시 두 시트가 자동으로 병합되어
모든 등록 사용자가 로그인 가능합니다 (이메일 중복 시 manual_add 우선).

매핑:
  PWC_ID  → 이메일
  EMPNO   → 사번
  EMPNM   → 이름
  ORG_NM  → 부서 (CM_NM은 상위 분류 - 'Global'식으로 너무 광범위하여 ORG_NM 사용)
  EMP_STAT → 활성 (재직/휴직 = Y, 그 외 = N)

실행: python scripts/update_users.py
"""
import json
import sys
from pathlib import Path

import pyodbc
import pandas as pd
from openpyxl.styles import Font

ROOT = Path(__file__).parent.parent.parent
CRED = json.loads((ROOT / "credentials.json").read_text(encoding="utf-8"))
USERS_FILE = ROOT / "2.사용자관리/users.xlsx"

AZURE = CRED.get("azure_sql", {})

SHEET_AUTO = "azure_auto"
SHEET_MANUAL = "manual_add"
HEADER = ["이메일", "사번", "이름", "부서", "활성"]


def fetch_from_azure() -> pd.DataFrame:
    """BI_STAFFREPORT_EMP_V에서 직원 정보 조회 → 표준 5컬럼 DataFrame"""
    if not AZURE.get("server") or not AZURE.get("password"):
        raise RuntimeError(
            "credentials.json에 azure_sql 설정이 없거나 비어있습니다.\n"
            '필요 항목: {"server","database","username","password"}'
        )

    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={AZURE['server']};DATABASE={AZURE['database']};"
        f"UID={AZURE['username']};PWD={AZURE['password']};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    print("Azure SQL 연결 중...")
    with pyodbc.connect(conn_str) as conn:
        df = pd.read_sql("""
            SELECT PWC_ID, EMPNO, EMPNM, CM_NM, ORG_NM, EMP_STAT
            FROM BI_STAFFREPORT_EMP_V
        """, conn).fillna("")
    print(f"  {len(df):,}명 원본 조회")

    # 부서: ORG_NM 우선, 비어있으면 CM_NM fallback
    def _dept(row):
        org = str(row.get("ORG_NM", "")).strip()
        cm = str(row.get("CM_NM", "")).strip()
        return org if org else cm

    out = pd.DataFrame({
        "이메일": df["PWC_ID"].astype(str).str.strip().str.lower(),
        "사번":   df["EMPNO"].astype(str).str.strip(),
        "이름":   df["EMPNM"].astype(str).str.strip(),
        "부서":   df.apply(_dept, axis=1),
        "활성":   df["EMP_STAT"].apply(
            lambda x: "Y" if str(x).strip() in ("재직", "휴직") else "N"
        ),
    })
    # 이메일·사번 누락 행 제거
    before = len(out)
    out = out[(out["이메일"] != "") & (out["사번"] != "")]
    if before != len(out):
        print(f"  이메일/사번 누락 {before - len(out)}명 제외")

    out = out.sort_values(["부서", "이름"]).reset_index(drop=True)
    return out


def read_manual_sheet() -> pd.DataFrame:
    """기존 users.xlsx에서 manual_add 시트 읽기. 없으면 빈 DataFrame."""
    if not USERS_FILE.exists():
        return pd.DataFrame(columns=HEADER)
    try:
        df = pd.read_excel(USERS_FILE, sheet_name=SHEET_MANUAL, dtype=str).fillna("")
    except (ValueError, KeyError):
        # 시트 없음 (구버전 단일 시트 파일인 경우 등)
        return pd.DataFrame(columns=HEADER)

    # 컬럼 보정
    for c in HEADER:
        if c not in df.columns:
            df[c] = ""
    df = df[HEADER]
    # 빈 행 제거 (이메일·사번 둘 다 비어있는 경우)
    df = df[(df["이메일"].str.strip() != "") | (df["사번"].str.strip() != "")]
    return df.reset_index(drop=True)


def write_users_xlsx(auto_df: pd.DataFrame, manual_df: pd.DataFrame) -> None:
    """두 시트를 단일 파일로 저장 (서식 포함)"""
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # manual 시트가 비어 있으면 안내용 행 1줄 추가
    if manual_df.empty:
        manual_df = pd.DataFrame([
            {"이메일": "example@samil.com", "사번": "999999",
             "이름": "수기추가예시 (활성=Y로 바꾸면 로그인 가능)",
             "부서": "외부협력", "활성": "N"},
        ], columns=HEADER)

    with pd.ExcelWriter(USERS_FILE, engine="openpyxl") as writer:
        auto_df.to_excel(writer, sheet_name=SHEET_AUTO, index=False)
        manual_df.to_excel(writer, sheet_name=SHEET_MANUAL, index=False)

        for sheet_name in (SHEET_AUTO, SHEET_MANUAL):
            ws = writer.sheets[sheet_name]
            for cell in ws[1]:
                cell.font = Font(bold=True)
            ws.column_dimensions["A"].width = 32
            ws.column_dimensions["B"].width = 10
            ws.column_dimensions["C"].width = 14
            ws.column_dimensions["D"].width = 30
            ws.column_dimensions["E"].width = 8
            ws.freeze_panes = "A2"


def main() -> None:
    print("=" * 60)
    print("  사용자 목록 갱신 (Azure SQL → users.xlsx)")
    print("=" * 60)

    # 1. 기존 manual_add 시트 보존 (먼저 읽어둠 - 쓰기 전에 손상 방지)
    manual_df = read_manual_sheet()
    print(f"\n[manual_add] 기존 보존: {len(manual_df)}명")

    # 2. Azure 조회
    print()
    auto_df = fetch_from_azure()
    n_active = (auto_df["활성"] == "Y").sum()
    print(f"[azure_auto] 갱신 대상: {len(auto_df)}명 (활성 Y: {n_active}명)")

    # 3. 저장
    write_users_xlsx(auto_df, manual_df)

    print(f"\n저장 완료: {USERS_FILE}")
    print(f"  [azure_auto]  {len(auto_df)}명  (자동 갱신 - 이 스크립트가 덮어씀)")
    print(f"  [manual_add]  {len(manual_df)}명  (수기 관리 - 항상 보존)")
    print("\n다음 단계: cd 3.정적생성 && python build_portal.py")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[오류] {e}")
        sys.exit(1)
