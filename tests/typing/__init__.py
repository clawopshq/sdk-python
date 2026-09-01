"""타입 수준 회귀 테스트.

여기 있는 파일은 **실행되지 않는다** — 전부 ``if TYPE_CHECKING:`` 아래에 있고,
검증은 CI 의 mypy 단계가 한다(``.github/workflows/ci.yml``).

작동 원리: ``[tool.mypy] strict = true`` 는 ``warn_unused_ignores`` 를 켠다.
따라서 "에러가 나야 하는 코드"에 ``# type: ignore[<코드>]`` 를 붙여 두면,
그 에러가 **사라지는 순간** 그 ignore 가 "쓰이지 않았다"로 실패한다.
TypeScript 의 ``@ts-expect-error`` 와 같은 역할이다.

⚠️ ``tests/`` 전체는 strict 로 172건이 깨져 있어 게이트에 넣을 수 없다.
mypy 가 보는 테스트 디렉터리는 **이 디렉터리뿐**이다.
"""
