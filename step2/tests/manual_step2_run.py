import json
import os
import sys
import time

import django


# Django 설정을 먼저 초기화한 뒤 step2를 import 해야 한다.
if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.settings")
    django.setup()

from step2 import get_llm_service


SETTING_ID = "6e772c6c-07dc-11f1-84fc-6e2ea9f68e64"

# ANSI 색상
DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_debug(service, result):
    """마지막 턴의 프롬프트 입력값과 LLM 응답을 출력한다."""
    logs = service.get_prompt_logs()
    if not logs:
        return
    last = logs[-1]

    print(f"\n{DIM}{'=' * 60}")
    print(f"[DEBUG] sequence={last['sequence']}  persona={last['persona']}")
    print(f"{'=' * 60}{RESET}")

    # system instruction 항상 전체 출력
    print(f"\n{CYAN}--- system_instruction ({len(last['system_instruction'])}자) ---{RESET}")
    print(f"{DIM}{last['system_instruction']}{RESET}")

    # history
    history = last["history"]
    if history:
        print(f"\n{YELLOW}--- history ({len(history)}건) ---{RESET}")
        for i, h in enumerate(history):
            role_color = GREEN if h["role"] == "user" else RED
            content = h["parts"][0] if h["parts"] else ""
            print(f"  {DIM}[{i}] {role_color}{h['role']}{RESET}{DIM}: {content}{RESET}")

    # prompt (실제 send_message에 들어간 값)
    print(f"\n{GREEN}--- prompt (send_message) ---{RESET}")
    print(f"{last['prompt']}")

    # LLM 응답 원본
    print(f"\n{RED}--- LLM response (raw) ---{RESET}")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"\n{DIM}{'=' * 60}{RESET}")


def main() -> None:
    """
    실제 DB와 Gemini를 사용해서 step2 플로우를 테스트하는 스크립트.
    --debug 플래그를 붙이면 LLM 입출력을 모두 볼 수 있다.
    """
    debug = "--debug" in sys.argv

    service = get_llm_service()
    setting_id = SETTING_ID

    print("=== step2 LLM 테스트 ===")
    print(f"- setting_id: {setting_id}")
    print(f"- debug: {debug}")
    print("종료하려면 언제든지 'q' 를 입력하세요.\n")

    user_answer: str | None = None

    while True:
        try:
            start = time.time()
            result = service.generate_question(setting_id, user_answer=user_answer)
            elapsed = time.time() - start
        except ValueError as e:
            print(f"\n[종료] 더 이상 질문을 생성할 수 없습니다: {e}")
            break
        except Exception as e:  # pragma: no cover
            print(f"\n[오류] 질문 생성 중 예외 발생: {e}")
            break

        if debug:
            print_debug(service, result)

        sequence = result.get("sequence")
        next_question = result.get("next_question")
        face = result.get("face")

        if result.get("fin"):
            print("\n========================================")
            print(f"[면접 종료] (표정: {face}) {DIM}(Gemini 응답: {elapsed:.2f}s){RESET}")
            print(next_question)
            print("========================================")
            break

        print("\n----------------------------------------")
        print(f"[질문 {sequence}] (표정: {face}) {DIM}(Gemini 응답: {elapsed:.2f}s){RESET}")
        print(next_question)
        print("----------------------------------------")

        user_input = input("\n당신의 답변을 입력하세요 (종료: q): ").strip()
        if user_input.lower() == "q":
            print("\n사용자에 의해 종료되었습니다.")
            break

        user_answer = user_input if user_input else None


if __name__ == "__main__":
    main()

