"""
dart_key_manager.py — DART API 키 순환 관리

credentials.json의 dart.api_keys 목록을 순서대로 사용.
DART 일일 한도 초과(status "020") 감지 시 자동으로 다음 키로 전환.

사용법:
    from dart_key_manager import KEY_MGR
    params = {...}               # crtfc_key 제외
    data = await _get(client, url, params)  # KEY_MGR.current 자동 주입
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.parent  # 종합기업정보/

# 이 status 코드 수신 시 다음 키로 전환
ROTATE_STATUSES = {
    "020",  # 요청 제한 횟수 초과 (일일 한도)
    "010",  # 등록되지 않은 키
    "011",  # 사용할 수 없는 키
}


class DartKeyManager:
    def __init__(self) -> None:
        cred = json.loads((ROOT / "credentials.json").read_text(encoding="utf-8"))
        dart = cred["dart"]
        self.keys: list[str] = dart.get("api_keys") or [dart["api_key"]]
        self._idx = 0

    @property
    def current(self) -> str:
        return self.keys[self._idx]

    def should_rotate(self, status: str) -> bool:
        return status in ROTATE_STATUSES

    def rotate(self, status: str = "") -> bool:
        """다음 키로 전환. 성공이면 True, 모든 키 소진이면 False."""
        _reason = {"020": "요청한도초과", "010": "미등록키", "011": "사용불가키"}.get(status, status or "?")
        if self._idx + 1 >= len(self.keys):
            print(f"\n  [DART 키 전부 소진] {len(self.keys)}개 키 모두 한도 초과 (status={status} {_reason})")
            return False
        self._idx += 1
        print(f"\n  [DART 키 전환] → 키 #{self._idx + 1}/{len(self.keys)} 사용 중 (status={status} {_reason})")
        return True

    @property
    def exhausted(self) -> bool:
        return self._idx >= len(self.keys)

    def status_label(self) -> str:
        return f"키 #{self._idx + 1}/{len(self.keys)}"


# 스크립트별로 각자 인스턴스 생성 (import 시점에 초기화)
KEY_MGR = DartKeyManager()
