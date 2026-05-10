"""
BI_STAFFREPORT_RETAIN_V → 프로젝트 마스터 추출
================================================
- PRJTCD / PRJTNM 고유값 추출
- ../exclude_keywords.json 에 등록된 키워드가 포함된 프로젝트 제외
- 웹 검색용 JSON으로 저장 (../output/project_master.json)

제외 키워드 추가 방법:
  ../exclude_keywords.json 의 "keywords" 배열에 단어 추가 후 재실행
"""

import pyodbc
import pandas as pd
import json
import os
import sys
from datetime import datetime

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ────────────────────────────────────────────────────────────
# 경로 설정
# ────────────────────────────────────────────────────────────

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
BASE_DIR      = os.path.dirname(SCRIPT_DIR)          # 1.회사명정리/
OUTPUT_DIR    = os.path.join(BASE_DIR, "output")
KEYWORDS_FILE = os.path.join(BASE_DIR, "exclude_keywords.json")

# ────────────────────────────────────────────────────────────
# DB 설정
# ────────────────────────────────────────────────────────────

DB_CONFIG = {
    "server":   "gx-zsesqlp011.database.windows.net",
    "database": "REPORT_COMMON",
    "username": "KRAzureCommon",
    "password": "a=fh9+@Xw?4RgprbFD2TQ*eUgLB8R7eL",
}

SOURCE_TABLE = "BI_STAFFREPORT_RETAIN_V"

# ────────────────────────────────────────────────────────────
# 제외 키워드 로드
# ────────────────────────────────────────────────────────────

def load_exclude_keywords() -> list:
    if not os.path.exists(KEYWORDS_FILE):
        print(f"[경고] {KEYWORDS_FILE} 없음 - 제외 키워드 없이 진행")
        return []
    with open(KEYWORDS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    keywords = [kw.strip() for kw in data.get("keywords", []) if kw.strip()]
    print(f"제외 키워드 {len(keywords)}개 로드: {keywords}")
    return keywords

# ────────────────────────────────────────────────────────────
# DB 연결 및 추출
# ────────────────────────────────────────────────────────────

def get_connection():
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={DB_CONFIG['server']};"
        f"DATABASE={DB_CONFIG['database']};"
        f"UID={DB_CONFIG['username']};"
        f"PWD={DB_CONFIG['password']};"
        f"Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(conn_str)


def fetch_projects(conn) -> pd.DataFrame:
    query = f"""
    SELECT DISTINCT
        PRJTCD,
        PRJTNM
    FROM [{SOURCE_TABLE}]
    WHERE PRJTCD  IS NOT NULL
      AND PRJTNM  IS NOT NULL
      AND LTRIM(RTRIM(PRJTCD))  <> ''
      AND LTRIM(RTRIM(PRJTNM))  <> ''
    ORDER BY PRJTNM
    """
    print(f"\n'{SOURCE_TABLE}' 조회 중...")
    df = pd.read_sql(query, conn)
    df["PRJTCD"] = df["PRJTCD"].astype(str).str.strip()
    df["PRJTNM"] = df["PRJTNM"].astype(str).str.strip()
    print(f"  원본 고유값: {len(df):,}건")
    return df

# ────────────────────────────────────────────────────────────
# 필터링
# ────────────────────────────────────────────────────────────

def filter_projects(df: pd.DataFrame, keywords: list) -> pd.DataFrame:
    if not keywords:
        return df

    mask = pd.Series([False] * len(df), index=df.index)
    for kw in keywords:
        mask |= df["PRJTNM"].str.contains(kw, case=False, na=False)

    excluded = df[mask]
    filtered = df[~mask].reset_index(drop=True)

    print(f"\n  제외된 프로젝트 ({len(excluded):,}건):")
    for _, row in excluded.iterrows():
        print(f"    [{row['PRJTCD']}] {row['PRJTNM']}")

    print(f"\n  최종 프로젝트: {len(filtered):,}건")
    return filtered

# ────────────────────────────────────────────────────────────
# 저장
# ────────────────────────────────────────────────────────────

def save_outputs(df: pd.DataFrame, keywords: list):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    payload = {
        "generated_at":      datetime.now().isoformat(timespec="seconds"),
        "source_table":      SOURCE_TABLE,
        "excluded_keywords": keywords,
        "total":             len(df),
        "projects": [
            {"id": row["PRJTCD"], "name": row["PRJTNM"]}
            for _, row in df.iterrows()
        ],
    }

    json_path = os.path.join(OUTPUT_DIR, "project_master.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n  [OK] JSON: {json_path}")

    csv_path = os.path.join(OUTPUT_DIR, f"project_master_{ts}.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"  [OK] CSV : {csv_path}")

    return json_path

# ────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  프로젝트 마스터 추출 (웹 검색 기초자료)")
    print("=" * 60)

    keywords = load_exclude_keywords()

    print("\nAzure SQL 연결 중...")
    try:
        conn = get_connection()
    except Exception as e:
        print(f"\n[오류] 연결 실패: {e}")
        input("Enter 키를 누르면 종료됩니다...")
        return
    print("[OK] 연결 성공!")

    try:
        df_raw      = fetch_projects(conn)
        df_filtered = filter_projects(df_raw, keywords)
        save_outputs(df_filtered, keywords)
    finally:
        conn.close()

    print("\n" + "=" * 60)
    print(f"  완료! 총 {len(df_filtered):,}개 프로젝트")
    print("=" * 60)
    input("\nEnter 키를 누르면 종료됩니다...")


if __name__ == "__main__":
    main()
