# step2 내용을 interviewer.py에 적용하는 계획

## 1. 현재 구조 비교

| 구분 | 현재 `interview/interviewer.py` | step2 |
|------|--------------------------------|--------|
| **페르소나** | 하드코딩 `PERSONA_PROMPTS` dict (CASUAL/FORMAL/PRESSURE) | 파일 기반 `PersonaService` (`role.txt`, `personality.txt`, `rules.txt`) |
| **시스템 프롬프트** | 페르소나 + 간단 규칙 + `EXPRESSION_GUIDE` | 페르소나 파일에 질문 수/진행 단계, 표정 규칙, 답변 품질 대응까지 포함 |
| **LLM 응답 형식** | 자유 텍스트 + `[expression:TAG]` 정규식 파싱 | **JSON 스키마** (`response_schema`)로 구조화 응답 |
| **표정(expression/face)** | neutral, smile, serious, thinking, surprised | happy, neutral, thinking, serious, smile, curious, **encouraging** (7종, TTS/3D에 맞춤) |
| **세션/컨텍스트** | `InterviewSession` 인스턴스별 `history`만 사용 | `setting_id` 기준 DB 연동 (`LLMContextService`: Skill, PreQuestion, position 등) |
| **API 스타일** | 비동기 `generate_first_question()`, `process_answer(user_text)` | 동기 `generate_question(setting_id, user_answer?)` |

**호출 측 (`signaling/interview_handler.py`) 기대값:**

- `InterviewSession(persona=..., max_questions=...)`
- `generate_first_question()` → `{ "text", "expression" }`
- `process_answer(user_text)` → `{ "text", "expression", "finished" }`
- `question_count`, `max_questions`, `history` 속성

→ **이 공개 API는 유지**해야 하며, 내부만 step2 방식으로 바꾼다.

---

## 2. 적용 계획 (단계별)

### Phase 1: 페르소나를 파일 기반으로 통일 (breaking 없음)

**목표:** 하드코딩 `PERSONA_PROMPTS` 제거, step2의 `PersonaService` + persona 디렉터리 사용.

1. **페르소나 파일 위치**
   - **옵션 A:** `step2/utils/personas/` 그대로 두고 `interview/interviewer.py`에서 `step2.utils.personas.PersonaService` import.
   - **옵션 B:** `interview/personas/` 로 persona 디렉터리 복사·이동 후, `interview` 전용 `PersonaService`(또는 step2 것 재사용) 사용.

   **권장:** 옵션 A. step2와 한 소스로 유지하고, 나중에 step2 제거 시에만 옵션 B로 이전.

2. **작업 내용**
   - `interview/interviewer.py`에서 `PERSONA_PROMPTS` 제거.
   - `PersonaService.get_persona(persona_name)`으로 시스템 프롬프트(페르소나) 문자열 획득.
   - `_build_system_prompt()`에서 기존 `persona_text = PERSONA_PROMPTS.get(...)` 대신 `PersonaService.get_persona(self.persona)` 사용.
   - 기본값은 step2와 동일하게 `FORMAL` 유지.

3. **검증**
   - 기존처럼 `InterviewSession(persona="FORMAL")`, `"CASUAL"`, `"PRESSURE"` 생성 후 `generate_first_question()` 호출해 동작 확인.

---

### Phase 2: LLM 응답을 JSON 스키마로 전환 (표정 필드 매핑)

**목표:** 자유 텍스트 + `[expression:TAG]` 파싱 제거, Gemini `response_schema`로 JSON 응답 받기.

1. **스키마 도입**
   - step2의 `LLM_RESPONSE_JSON_SCHEMA`(또는 interviewer용으로 필수 필드만 정리한 스키마) 사용.
   - 필수 필드: `next_question`, `face` (그 외 `sequence`, `before_user_answer` 등은 선택적으로 활용).

2. **Gemini 호출 방식 변경**
   - 현재: `model.generate_content(contents, ...)` → 텍스트 반환 → `_parse_response(raw)`.
   - 변경: `genai.types.GenerationConfig`에 `response_mime_type="application/json"`, `response_schema=스키마` 설정 후 파싱.
   - **비동기 유지:** `call_gemini`는 그대로 두고, 내부에서 `run_in_executor`로 동기 Gemini 호출(JSON 모드) 실행.

3. **표정(expression) 호환**
   - step2는 `face` enum: `happy`, `neutral`, `thinking`, `serious`, `smile`, `curious`, `encouraging`.
   - handler는 `expression` 키를 기대하므로, **반환 시 `face` → `expression`으로 이름만 매핑**하면 됨.
   - 기존 `expression` 값(surprised 등)은 step2 규칙에 없으면 제거하고, 7종으로 통일해도 무방.

4. **작업 내용**
   - `interviewer.py`에 JSON 스키마 상수 또는 step2 `schemas`에서 import.
   - `_call_gemini_sync` (또는 JSON 전용 내부 함수)에서 `response_schema` 사용, 응답 `json.loads` 후 `next_question` → `text`, `face` → `expression`으로 변환.
   - `_parse_response` 제거 또는 JSON 경로에서만 사용하지 않도록 정리.
   - `generate_first_question` / `process_answer` / `_generate_closing` 모두 동일한 JSON 호출·파싱 경로 사용.

5. **검증**
   - 첫 질문·중간 답변·마무리까지 호출해 `text`, `expression`(face 매핑), `finished`가 handler 기대와 일치하는지 확인.

---

### Phase 3: (선택) setting_id·DB 컨텍스트 연동

**목표:** 실시간 면접 세션에 `setting_id`를 넘겨, step2의 `LLMContextService`처럼 포지션·스킬·사전질문 등을 시스템 프롬프트에 포함.

1. **API 확장**
   - `InterviewSession.__init__(self, persona=..., max_questions=..., setting_id=None)` 처럼 `setting_id`를 옵션으로 추가.
   - `setting_id`가 있을 때만 `LLMContextService.get_setting_context(setting_id)` 호출해 시스템 프롬프트에 "[면접 설정 정보]..." 블록 추가 (step2와 동일 포맷).

2. **호출 측 수정**
   - `signaling/interview_handler.py`의 `_start_interview`에서, room 또는 메시지에서 `setting_id`를 받아올 수 있다면 `InterviewSession(..., setting_id=setting_id)` 로 전달.
   - 없으면 기존처럼 `setting_id=None`으로 동작 (현재와 동일).

3. **의존성**
   - `LLMContextService`는 Django ORM(`InterviewSetting` 등) 사용하므로, `interview` 앱이 `ai_bot.models` 등을 참조 가능한 구조인지 확인 (이미 step2에서 사용 중이면 동일 설정으로 가능).

4. **검증**
   - `setting_id` 있는 세션에서 스킬/사전질문이 프롬프트에 포함되는지, 로그 또는 테스트로 확인.

---

## 3. 구현 시 주의사항

- **비동기 유지:** `generate_first_question`, `process_answer`는 계속 `async`이고, 내부 Gemini 호출만 `run_in_executor`로 처리.
- **하위 호환:** 반환 형태 `{ "text", "expression", "finished" }` 및 `InterviewSession`의 `history`, `question_count`, `max_questions`는 그대로 유지.
- **에러 처리:** JSON 파싱 실패 시 fallback(기존 텍스트 파싱 또는 재시도) 여부를 정책으로 결정.
- **설정 키:** Gemini API 키는 현재처럼 `interviewer.py`에서 `django.conf.settings` 또는 `os.environ` 사용 (step2는 `dotenv` 직접 사용 중이므로, Django 설정과 통일하면 좋음).

---

## 4. 작업 순서 요약

| 순서 | 내용 | 비고 |
|------|------|------|
| 1 | PersonaService 도입, PERSONA_PROMPTS 제거 | Phase 1 |
| 2 | JSON 스키마 + response_schema 적용, face→expression 매핑 | Phase 2 |
| 3 | (선택) setting_id, LLMContextService 연동 | Phase 3 |

이 순서대로 적용하면, 기존 `interview_handler` 수정 없이 interviewer 내부만 step2 방식으로 정리할 수 있다.
