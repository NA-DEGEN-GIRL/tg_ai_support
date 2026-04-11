# tg_self_reply

내 텔레그램 user account 가 보낸 메시지를 듣고 키워드 매칭 시 액션을 디스패치하는 데몬.
봇 계정이 아니라 user account 를 TDLib (`libtdjson.so`) 의 ctypes 바인딩으로 직접 사용함.

## 실행

```
~/codes/na_log_bot/3.10/bin/python main.py
```

첫 실행 시 phone → 인증코드 → 2FA 입력. 세션은 `tdlib/` 에 저장돼서 두 번째부터는 입력 없음.
`.env` 에 `OPENAI_API_KEY` (필수) 와 `GEMINI_API_KEY` (선택) 를 넣어두기. `.env.example` 참고.

## 파일 구조

- `main.py` — 단일 파일. 이벤트 루프 + 액션 디스패치 + AI 클라이언트 + 영속 로직 전부.
- `config.json` — `tdjson_path`, `api_id`, `api_hash`, `ai.models`, `ai.default_model`, `rules`. **mtime 핫 리로드** 됨. (gitignored, `config.json.example` 복사해서 사용)
- `.env` — API 키들 (gitignored). `.env.example` 복사해서 사용.
- `messages.json` — 최근 본 self-message 스냅샷 (gitignored). 60초마다 dirty 일 때만 저장.
- `state.json` — `current_model` 등 런타임 상태 (gitignored). 모델 변경 시점에 저장.
- `tdlib/` — TDLib 자체 세션 DB (gitignored).

## 액션 (action) 종류

`config.json` 의 `rules` 각 항목에 `action` 필드:

| action | 동작 | 예시 |
|---|---|---|
| `reply` | 정적 텍스트 답장 | `"테스트"` → `"test done"` |
| `translate` | AI 로 번역 후 답장 | `"오늘 뭐함 to en"` → `"What are you up to today"` |
| `ask_ai` | AI 에 질문 후 답장 | `"파이썬 list comprehension 예시 to ai"` |
| `ask_ai` + `search:true` | 웹검색 grounding 후 답장 | `"비트코인 시세 to web"` |
| `set_model` | 현재 사용 모델 변경 | `"ai model to gpt-5.4-pro"` |

매칭 모드 (`match`): `exact` / `contains` / `prefix` / `suffix`. 모두 case-insensitive (exact / contains 제외 — exact 는 strict, contains 는 strict).

## 번역 동작 (translate)

같은 키워드 (`to en` 등) 가 두 가지 컨텍스트로 동작:

1. **내 메시지 번역** — 일반 메시지 끝에 `to en` → 내 텍스트에서 suffix 떼고 번역.
   - `"오늘 날씨 좋다 to en"` → 답장: `"The weather is nice today"`
2. **상대 메시지 번역** — 누군가의 메시지에 reply 로 `to kr` → TDLib 의 `getMessage` 로 원본 가져와서 번역.
   - 상대: `"How's it going?"`
   - 나 (reply): `"to kr"`
   - 답장: `"잘 지내?"`

판단 기준: 내 outgoing 메시지의 `reply_to.@type == messageReplyToMessage` 가 있으면 case 2, 없으면 case 1.

## 일반 AI Q&A (ask_ai)

키워드: `to ai` (suffix). 동일하게 두 가지 컨텍스트:
- **standalone**: `"양자역학 한 줄로 설명 to ai"` → AI 답변
- **reply context**: 상대 메시지에 reply 로 `"to ai"` → 그 메시지를 prompt 로 AI 답변
  - reply 에 추가 텍스트 있으면 instruction 으로 prepend: `"in haiku to ai"` → `"in haiku\n\n---\n<원본>"`

### 웹검색 grounding (`search: true`)

`ask_ai` rule 에 `"search": true` 를 붙이면 provider 의 native 웹검색이 활성화됨. 기본 rule 은 `to web` (suffix). standalone / reply context 양쪽 모두 `to ai` 와 동일하게 작동하고 두 rule 이 공존함 — 빠른 Q&A 는 `to ai`, 최신 정보 필요하면 `to web` 로 분리.

provider 별 구현 (`search=True` 일 때):
- **OpenAI**: `/v1/chat/completions` 대신 `/v1/responses` 로 POST, `tools: [{"type": "web_search"}]` 추가. chat completions 엔 gpt-5.x 용 `web_search` 툴이 없어서 Responses API 가 유일한 길. 응답 파싱도 달라짐: `choices[0].message.content` 대신 `output[]` 배열에서 `type=="message"` 아이템 찾아 `content[].type=="output_text"` 텍스트 추출 (top-level `output_text` convenience field 있으면 우선 사용).
- **Gemini**: `generateContent` 엔드포인트 그대로. payload 에 `tools: [{"google_search": {}}]` 만 추가 (gemini-2.x / 3.x 용 tool name; gemini-1.5 는 `google_search_retrieval` 로 달랐음). 응답 포맷은 동일, `groundingMetadata` 가 같이 오지만 본문만 쓰면 됨.

timeout 은 검색 경로에서 60s → 120s 로 늘림 (웹검색 round-trip 대비).

## 모델 변경 (set_model)

`ai model to <model_name>` (prefix). 예: `ai model to gemini-2.5-pro`.
- `config.json` 의 `ai.models` 에 등록된 모델만 허용.
- 변경되면 `state.json` 에 저장. 데몬 재시작해도 유지.

## Markdown → Telegram HTML 변환

AI 출력 (특히 GPT 류) 은 standard markdown (`**bold**`, `*italic*`, `` `code` ``, ```` ```block``` ````, `[text](url)`, `# header`) 을 뱉음. 텔레그램은 그걸 그대로 못 알아먹어서 (텔레그램 markdown v2 는 `*bold*` 단일 asterisk 임) 변환 필요.

방식:
1. `md_to_telegram_html(text)` — 정규식으로 standard md → 텔레그램 HTML subset (`<b>`, `<i>`, `<s>`, `<code>`, `<pre>`, `<a>`) 변환. 코드블록과 링크 URL 은 placeholder 로 stash 했다가 복원해서 inline 처리에 안 망가지게. word-boundary lookaround 로 `foo_bar`, `2*x*5` 같은 식별자/수식 보호.
2. `td_execute({@type: parseTextEntities, parse_mode: textParseModeHTML})` — TDLib 의 synchronous static method 로 HTML → `formattedText` (text + entities) 파싱.
3. `sendMessage` 에 그 `formattedText` 를 그대로 박음.

파싱 실패 (잘못된 HTML 등) 시 fallback: 원본 텍스트를 entity 없이 plain 으로 보냄. `[fmt] HTML parse failed (...)` 로그.

`format_text` 는 `send_reply` 안에서 호출되므로 모든 답장 (정적 reply / 번역 결과 / AI 답변 / set_model 응답 / 에러 메시지) 에 자동 적용됨.

## AI 호출 흐름

- `call_ai(prompt, model=None, search=False)` 가 디스패처. `model` 생략 시 `get_current_model()` 사용. `search=True` 면 provider 별 웹검색 경로로 라우팅 (OpenAI Responses API / Gemini `google_search` tool).
- `get_provider(model)` 가 `ai.models` dict 에서 provider 찾아냄 (openai / gemini).
- `httpx.AsyncClient` 로 직접 HTTP POST. SDK 의존성 없음.
  - OpenAI (비검색): `https://api.openai.com/v1/chat/completions`
  - OpenAI (검색): `https://api.openai.com/v1/responses`
  - Gemini: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` (검색 여부는 payload 의 `tools` 필드로)
- AI 호출은 `asyncio.create_task` 로 spawn → 이벤트 루프 블로킹 안 됨.

## TDLib request/response correlation

`getMessage` 같은 동기 응답이 필요한 호출은 `td_request(query)` 사용:
1. uuid 로 `@extra` 태그 부여
2. `_pending_requests[req_id] = Future`
3. `td_send`
4. event_loop 에서 `update["@extra"]` 매칭되면 future 에 결과 set
5. `await asyncio.wait_for(fut, timeout)` 로 결과 받기

이걸로 case 2 번역에서 원본 메시지 fetch 가능.

## Config hot reload

- `get_config()` 가 매 호출마다 `config.json` 의 mtime 체크 → 변경되면 reparse + `[config] reloaded` 로그.
- `get_rules()`, `get_ai_section()`, `get_default_model()`, `get_provider()` 등 모두 `get_config()` 통해서 접근 → 자동으로 hot reload.
- 단, 부트스트랩 필드 (`api_id`, `api_hash`, `tdjson_path`, `max_recent_messages`, `save_interval_seconds`) 는 시작 시 한 번만 읽음. 바꾸려면 재시작.

## 영속성 / dedup

- 인메모리 `recent_messages` deque (`maxlen=500`) 가 dedup window + raw 로그.
- 60초마다 dirty 일 때만 `messages.json` 으로 atomic snapshot.
- `state.json` 은 `current_model` 변경 시점에 즉시 저장.
- 트레이드오프 (사용자 결정): 데몬 크래시 시 최대 60초간 dedup 정보 손실 → 매우 드물게 더블 액션 가능. SQLite/replay 같은 무거운 영속성은 의도적으로 안 씀.

## 로그 정책

- `[me]` 로 사용자 메시지 + 매칭 결과:
  ```
  [me] 친구챗 | '오늘 날씨 to en'  [translate: 'to en']
  [me] -> (translate) 'The weather today'
  ```
- `[init]`, `[auth]`, `[load]`, `[save]`, `[state]`, `[config]`, `[exit]` 는 데몬 상태 전환 / 에러.
- TDLib 자체 stderr 는 verbosity 0 (FATAL only) 로 막음.
- `config.debug = true` 로 raw update 토글 (`[debug]` 라인).

## 의도적으로 안 한 것

- **SQLite / DB / replay**: personal 데몬에 과함. JSON 스냅샷으로 충분.
- **OpenAI/Gemini SDK 의존성**: `httpx` 가 venv 에 이미 있고, 직접 HTTP 호출이 더 가벼움.
- **python-dotenv**: 10줄짜리 stdlib 파서로 충분.
- **정확한 reply 결과 영속화**: AI 결과는 채팅에 이미 남아 있으므로 messages.json 에 굳이 안 저장. 원본 메시지만 저장.
- **요청 큐 / rate limit**: 사용자가 분당 수십 번씩 트리거할 일 없음.

## 참고

- `~/codes/gpt_cli/gptcli.py` — 사용자의 다른 GPT 클라이언트. OpenRouter + openai SDK 패턴 (이 데몬과는 다름).
- `~/codes/na_log_bot/na_log_bot.py` — 같은 td_json ctypes 패턴의 reference. 답장 sendMessage 호출 형식이 거기와 동일.
- TDLib 1.8.46 (`/home/ubuntu/td/tdlib/lib/libtdjson.so`).
