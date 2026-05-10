"""
import_review.py
================
../output/review_needed.xlsx 에서 채워진 'official_name_입력란' 값을
../company_overrides.json 에 자동 반영.

사용법:
  python import_review.py

실행 후 python build_company_master.py 를 다시 실행하면 오버라이드 적용됩니다.
"""

import sys, os, json
import pandas as pd

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
BASE_DIR       = os.path.dirname(SCRIPT_DIR)          # 1.회사명정리/
OUTPUT_DIR     = os.path.join(BASE_DIR, "output")
REVIEW_EXCEL   = os.path.join(OUTPUT_DIR, "review_needed.xlsx")
OVERRIDES_FILE = os.path.join(BASE_DIR, "company_overrides.json")


def main():
    print("=" * 50)
    print("  review_needed.xlsx → company_overrides.json")
    print("=" * 50)

    if not os.path.exists(REVIEW_EXCEL):
        print(f"[오류] {REVIEW_EXCEL} 파일이 없습니다.")
        return

    df = pd.read_excel(REVIEW_EXCEL, sheet_name="검토필요", dtype=str).fillna("")
    name_col = "official_name_입력란"
    cand_col = "candidate_name"

    if name_col not in df.columns:
        print(f"[오류] '{name_col}' 열이 없습니다.")
        return

    filled = df[df[name_col].str.strip() != ""][[cand_col, name_col]].copy()
    filled[name_col] = filled[name_col].str.strip()
    filled[cand_col] = filled[cand_col].str.strip()

    if filled.empty:
        print("입력된 값이 없습니다. Excel에서 'official_name_입력란' 열을 채워주세요.")
        return

    print(f"입력값 {len(filled)}건 감지")

    if os.path.exists(OVERRIDES_FILE):
        with open(OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"_comment": [], "overrides": {}}

    overrides = data.get("overrides", {})
    added, updated = 0, 0

    for _, row in filled.iterrows():
        cand  = row[cand_col]
        value = row[name_col]
        if cand in overrides:
            if overrides[cand] != value:
                print(f"  [업데이트] {cand!r}: {overrides[cand]!r} → {value!r}")
                overrides[cand] = value
                updated += 1
        else:
            print(f"  [추가]     {cand!r} → {value!r}")
            overrides[cand] = value
            added += 1

    data["overrides"] = overrides
    with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {added}건 추가, {updated}건 업데이트")
    print(f"저장: {OVERRIDES_FILE}")
    print(f"\n다음: python build_company_master.py 재실행")


if __name__ == "__main__":
    main()
