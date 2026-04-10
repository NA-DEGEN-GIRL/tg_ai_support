# tg_self_reply

내 텔레그램 **user account** 가 보낸 메시지를 듣고, 키워드 매칭 시 자동으로 답장 / 번역 / AI 질문 / 모델 변경 같은 액션을 수행하는 데몬. **봇 계정이 아니라 user account 자체** 를 사용하기 때문에 TDLib 의 JSON 인터페이스 (`libtdjson.so`) 를 ctypes 로 직접 바인딩함.

---

## 소개

텔레그램 봇 API 는 `Bot` 계정으로만 동작함. 내 계정 (user account) 으로 메시지를 보내거나 자동 응답하려면 MTProto 를 직접 다루거나 TDLib 같은 라이브러리가 필요함. 이 프로젝트는 후자.

핵심 아이디어:

1. 내 계정으로 TDLib 세션을 띄움 (서버에서 백그라운드 데몬으로 돌림)
2. 핸드폰 / 데스크탑 등 **다른 디바이스에서** 내가 메시지를 보내면 → TDLib 가 `updateNewMessage` 이벤트로 그걸 데몬에 알려줌
3. 데몬이 그 메시지를 보고 룰에 매칭되면 → 내 계정으로 답장 (마찬가지로 user account 에서 보내는 거라 받는 사람 입장에서는 그냥 내가 답장한 것처럼 보임)

용도:
- 자주 쓰는 짧은 답변 자동화
- 외국인 친구한테 모국어로 쓰고 자동 번역해서 보내기
- 모르는 언어 메시지 받았을 때 reply 한 줄로 번역
- 채팅 중에 AI 한테 빠르게 물어보기

---

## 주요 특징

- **User account 직접 사용**: 봇이 아니라 내 계정. TDLib (`libtdjson.so`) 1.8.x 를 ctypes 로 바인딩.
- **Event-driven**: `updateNewMessage` 이벤트 기반. polling 안 함 → 빠르고 누락 적음.
- **4가지 액션**:
  - `reply` — 정적 텍스트 답장
  - `translate` — AI 로 번역해서 답장 (4개 언어 기본 제공: en/jp/cn/kr)
  - `ask_ai` — AI 에 질문 후 답장
  - `set_model` — 사용 중인 AI 모델 런타임 변경
- **번역 두 가지 컨텍스트**: 같은 키워드 (`to en` 등) 가 두 가지로 동작
  - 내 메시지 끝에 붙이면 → 내 텍스트를 번역
  - 누군가에 reply 로 보내면 → 그 원본 메시지를 번역
- **AI provider 2개**: OpenAI (`gpt-5.4` 등), Google Gemini (`gemini-2.5-pro` 등). httpx 로 직접 REST 호출, SDK 의존성 없음.
- **Config hot reload**: `config.json` 수정하면 자동으로 다시 읽음 (mtime 체크). 룰 추가나 모델 변경에 데몬 재시작 불필요.
- **Loop prevention**: 데몬 자기 자신이 보낸 답장 메시지는 `sending_state` 로 필터링 → 답장이 다시 룰에 매칭되어서 무한 루프 도는 거 차단.
- **가벼운 영속성**: 인메모리 deque + 60초 JSON 스냅샷. SQLite 안 씀.
- **깔끔한 로그**: `[me]` 만 보임. TDLib 자체 stderr 는 verbosity 0 으로 차단.

---

## 디렉토리 구조

```
tg_self_reply/
├── main.py              단일 파일. 이벤트 루프 + 액션 디스패치 + AI 클라이언트
├── config.json          런타임 설정 (gitignored)
├── config.json.example  설정 템플릿
├── .env                 API 키 (gitignored)
├── .env.example         API 키 템플릿
├── requirements.in      uv 의 입력 (httpx 만 들어있음)
├── requirements.txt     uv pip compile 결과 (lock)
├── messages.json        최근 본 self-message 스냅샷 (gitignored, 런타임 생성)
├── state.json           current_model 등 런타임 상태 (gitignored)
├── tdlib/               TDLib 자체 세션 DB (gitignored, 첫 auth 시 생성)
├── README.md            이 파일
├── CLAUDE.md            Claude 용 프로젝트 컨텍스트
└── .gitignore
```

---

## 사전 준비

### 1. TDLib 빌드

`libtdjson.so` 가 시스템에 있어야 함. 빌드 안 되어 있으면:

```bash
# 의존성 설치
sudo apt-get install make git zlib1g-dev libssl-dev gperf php-cli cmake clang-14 libc++-14-dev libc++abi-14-dev

# 클론 + 빌드
cd ~
git clone https://github.com/tdlib/td.git
cd td
rm -rf build
mkdir build && cd build
CXXFLAGS="-stdlib=libc++" CC=clang-14 CXX=clang++-14 cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH=../tdlib ..
cmake --build . --target install
```

빌드 후 `~/td/tdlib/lib/libtdjson.so` 에 위치함. 다른 경로에 두면 `config.json` 의 `tdjson_path` 도 그에 맞게 수정.

자세한 빌드 가이드: https://tdlib.github.io/td/build.html

### 2. Python 3.10+ 와 uv

```bash
# uv 설치 (없으면)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Telegram API 자격증명

https://my.telegram.org → API development tools → 새 application 등록

받는 것:
- `api_id` (정수)
- `api_hash` (문자열)

이건 user account 가 아니라 **application 자격증명** 임. 어느 user account 로 로그인할지는 데몬 첫 실행 시 phone number 입력으로 결정.

### 4. AI API 키

- **OpenAI** (필수): https://platform.openai.com/api-keys → `sk-proj-...` 형식
- **Gemini** (선택): https://aistudio.google.com/app/apikey → 비워두면 gemini-* 모델 비활성화

---

## 설치

### 1. 프로젝트 셋업

```bash
cd ~/codes/tg_self_reply
```

### 2. 가상환경 생성 + 의존성 설치 (uv)

```bash
uv venv .venv
source .venv/bin/activate

uv pip compile requirements.in -o requirements.txt
uv pip sync requirements.txt
```

`requirements.in` 의 단일 의존성:
```
httpx
```

`uv pip compile` 이 lock file (`requirements.txt`) 를 만들고, `uv pip sync` 가 그걸로 venv 를 채움.

### 3. 설정 파일 복사

```bash
cp config.json.example config.json
cp .env.example .env
```

### 4. `config.json` 편집

```json
{
    "tdjson_path": "~/td/tdlib/lib/libtdjson.so",
    "api_id": 12345678,
    "api_hash": "your_api_hash_here",
    ...
}
```

`api_id` 와 `api_hash` 를 my.telegram.org 에서 받은 값으로 교체.

### 5. `.env` 편집

```
OPENAI_API_KEY=sk-proj-실제_키
GEMINI_API_KEY=실제_키_또는_빈값
```

### 6. 실행 (첫 인증)

```bash
python main.py
```

첫 실행에서 묻는 것:
```
Phone number (with country code, e.g. +821012345678): +82xxxxxxxxx
Login code: <텔레그램에서 받은 코드>
2FA password: <2단계 인증 비밀번호 (있는 경우)>
[auth] ready
[init] model=gpt-5.4
```

세션은 `tdlib/` 디렉토리에 저장됨. 두 번째 실행부터는 입력 안 받고 바로 동작.

---

## 사용법

### 1. 정적 답장 (`reply`)

`config.json` 의 `rules` 에 추가:
```json
{"match": "exact", "keyword": "테스트", "action": "reply", "text": "test done"}
```

핸드폰에서 어떤 채팅방에 `테스트` 라고 보내면:
```
나: 테스트
나 (데몬): test done   ← reply 형태
```

매칭 모드 (`match`):
- `exact`: 메시지 전체가 keyword 와 정확히 일치
- `contains`: 메시지 안에 keyword 가 포함됨 (case-sensitive)
- `prefix`: 메시지가 keyword 로 시작 (case-insensitive)
- `suffix`: 메시지가 keyword 로 끝남 (case-insensitive)

### 2. 번역 (`translate`)

#### Case 1: 내 메시지 번역

룰:
```json
{"match": "suffix", "keyword": "to en", "action": "translate", "lang": "English"}
```

핸드폰에서:
```
나: 오늘 날씨 좋다 to en
나 (데몬): The weather is nice today.   ← reply
```

처리 과정:
1. 메시지 끝의 `to en` 을 떼고 → 소스 텍스트 `오늘 날씨 좋다`
2. 현재 AI 모델에 번역 프롬프트 전송
3. 결과를 내 메시지에 reply 로 보냄

#### Case 2: 상대 메시지 번역

같은 룰. 다른 사용 방식.

```
친구: How's it going?
나 (그 메시지에 reply 로): to kr
나 (데몬): 잘 지내?   ← reply (내 to kr 메시지에 답장)
```

처리 과정:
1. 내 outgoing 메시지의 `reply_to.@type` 가 `messageReplyToMessage` 인지 확인
2. 맞으면 → TDLib 의 `getMessage` 로 원본 메시지 fetch
3. 그 텍스트를 번역해서 내 reply 메시지에 reply

기본 제공 언어:
- `to en` → English
- `to jp` → Japanese
- `to cn` → Chinese (Simplified)
- `to kr` → Korean

다른 언어 추가하고 싶으면 `config.json` 의 `rules` 에 같은 형식으로 추가:
```json
{"match": "suffix", "keyword": "to es", "action": "translate", "lang": "Spanish"}
```

저장만 하면 hot reload 됨.

### 3. AI 질문 (`ask_ai`)

룰:
```json
{"match": "suffix", "keyword": "to ai", "action": "ask_ai"}
```

#### Standalone (case 1)

```
나: 파이썬 list comprehension 한 줄 예시 to ai
나 (데몬): squares = [x**2 for x in range(10)]   ← AI 답변
```

`to ai` 떼고 → `파이썬 list comprehension 한 줄 예시` 를 prompt 로 전송.

#### Reply context (case 2)

상대 메시지를 AI 한테 물어볼 때:
```
친구: I'm thinking of starting a startup, what do you think?
나 (그 메시지에 reply): to ai
나 (데몬): <AI 의 분석 답변>
```

prompt 는 친구의 원본 메시지 그대로.

추가 instruction 을 붙이고 싶으면:
```
친구: I'm thinking of starting a startup
나 (reply): 한국어로 짧게 to ai
```

이때 prompt 는:
```
한국어로 짧게

---
I'm thinking of starting a startup
```

### 4. 모델 변경 (`set_model`)

룰:
```json
{"match": "prefix", "keyword": "ai model to ", "action": "set_model"}
```

```
나: ai model to gemini-2.5-pro
나 (데몬): [ok] model -> gemini-2.5-pro (gemini)
```

검증:
- `config.json` 의 `ai.models` 에 등록되지 않은 모델은 거부
- 모델 이름으로부터 provider (openai / gemini) 자동 판별
- 변경된 모델은 `state.json` 에 저장 → 데몬 재시작해도 유지

### 5. 룰 추가 (hot reload)

`config.json` 편집 → 저장. 다음 메시지가 들어올 때 mtime 체크해서 자동으로 다시 읽음:
```
[config] reloaded
```

데몬 재시작 안 해도 됨.

**주의**: hot reload 가 적용되는 건 `rules`, `ai.*`, `debug`. 나머지 (`api_id`, `api_hash`, `tdjson_path`, `max_recent_messages`, `save_interval_seconds`) 는 부트 시 한 번만 읽기 때문에 바꾸려면 재시작 필요.

### 6. 모델 추가

`config.json` 의 `ai.models.openai` 또는 `ai.models.gemini` 배열에 모델 ID 추가 → 저장. Hot reload 됨. 그 다음 `ai model to <new>` 로 전환 가능.

---

## 설정 파일

### `config.json`

```json
{
    "tdjson_path": "~/td/tdlib/lib/libtdjson.so",
    "api_id": 12345678,
    "api_hash": "...",
    "debug": false,

    "ai": {
        "default_model": "gpt-5.4",
        "models": {
            "openai": ["gpt-5.4", "gpt-5.4-mini", ...],
            "gemini": ["gemini-2.5-pro", ...]
        }
    },

    "rules": [
        {"match": "exact",  "keyword": "테스트", "action": "reply", "text": "test done"},
        {"match": "suffix", "keyword": "to en", "action": "translate", "lang": "English"},
        {"match": "suffix", "keyword": "to ai", "action": "ask_ai"},
        {"match": "prefix", "keyword": "ai model to ", "action": "set_model"}
    ]
}
```

| 필드 | 의미 | hot reload |
|---|---|---|
| `tdjson_path` | `libtdjson.so` 의 경로. `~` 자동 expand | ❌ |
| `api_id`, `api_hash` | my.telegram.org 에서 받은 application 자격증명 | ❌ |
| `debug` | true 면 모든 TDLib update 의 raw JSON preview 출력 | ✅ |
| `ai.default_model` | `state.json` 에 저장된 모델이 없을 때 fallback | ✅ |
| `ai.models` | provider → 허용된 모델 ID 리스트 | ✅ |
| `rules` | 매칭 룰 배열 | ✅ |
| `max_recent_messages` | (옵션, 기본 500) 인메모리 deque 최대 크기 | ❌ |
| `save_interval_seconds` | (옵션, 기본 60) 스냅샷 주기 | ❌ |

### `.env`

```
OPENAI_API_KEY=sk-proj-...
GEMINI_API_KEY=
```

| 변수 | 필수 | 비고 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ (gpt-* 사용 시) | https://platform.openai.com/api-keys |
| `GEMINI_API_KEY` | ❌ | 비우면 gemini-* 모델 호출 시 에러 응답 |

`.env` 는 데몬 시작 시 한 번만 읽음. 키 바꾸려면 재시작.

---

## 동작 원리

### TDLib 사용 이유

텔레그램에는 두 가지 클라이언트 인터페이스가 있음:

1. **Bot API** — `Bot` 계정으로만 동작. user 가 보낸 메시지에 user account 로 답장 불가능.
2. **MTProto / TDLib** — user account 든 bot 이든 사용 가능. 모든 텔레그램 기능 접근 가능.

이 프로젝트는 user account 에서 보낸 자기 자신의 메시지에 같은 user account 로 답장해야 하므로 (2) 필수. TDLib (`libtdjson.so`) 를 ctypes 로 바인딩.

### 이벤트 기반 처리

```python
async def event_loop():
    while True:
        update = await loop.run_in_executor(_recv_executor, td_receive, 1.0)
        if update is None:
            continue
        # update["@type"] 에 따라 분기
```

`td_receive` 는 블로킹 C 함수라서 단일 스레드 executor 에 위임. asyncio 이벤트 루프는 그 동안 다른 태스크 (AI 호출 등) 를 자유롭게 처리.

### 메시지 필터링

```python
if not message["is_outgoing"]:        # 내가 보낸 게 아니면 무시
    return
if message.get("sending_state"):       # 데몬 자기가 지금 보내고 있는 거면 무시 (loop 방지)
    return
```

`is_outgoing` 은 user account 본인이 보낸 메시지 (어느 디바이스에서든) 를 의미. `sending_state` 가 set 되어 있으면 그건 데몬 자체의 send 콜이 emit 한 in-flight 메시지라서 제외.

### 액션 디스패치

```python
rule = match_rule(text)
if rule:
    asyncio.create_task(dispatch_action(text, chat_id, message_id, reply_ctx, rule))
```

매칭되면 `create_task` 로 비동기 처리. 이벤트 루프는 즉시 다음 update 를 받으러 돌아감 → AI 호출이 5초 걸려도 다른 메시지 처리에 지장 없음.

### Reply context fetching (`td_request`)

번역/AI 의 case 2 (상대 메시지 처리) 를 위해서 원본 메시지를 fetch 해야 함. TDLib 의 `getMessage` 호출 후 응답을 기다리는 동기 패턴이 필요.

방법: `@extra` 필드에 uuid 를 박고, 응답이 같은 uuid 와 함께 오면 그 future 에 결과를 set:

```python
async def td_request(query, timeout=10):
    req_id = uuid.uuid4().hex
    query["@extra"] = req_id
    fut = loop.create_future()
    _pending_requests[req_id] = fut
    td_send(query)
    return await asyncio.wait_for(fut, timeout)

# event_loop 안에서:
extra = update.get("@extra")
if extra and extra in _pending_requests:
    _pending_requests[extra].set_result(update)
    continue
```

### Hot reload

```python
def get_config():
    mtime = CONFIG_PATH.stat().st_mtime
    if mtime == _config_mtime:
        return _config_cache  # 캐시 사용
    # mtime 변경됨 → reparse
    _config_cache = json.load(...)
    _config_mtime = mtime
    return _config_cache
```

`get_rules()`, `get_ai_section()`, `get_provider()` 등 설정에 의존하는 모든 함수가 `get_config()` 통해서 접근. 매 호출마다 mtime 만 비교 (file open 안 함) → 거의 무료.

### 영속성

세 가지 파일:

1. **`tdlib/td.binlog`** — TDLib 자체. 세션 + update sequence number. TDLib 가 알아서 관리.
2. **`messages.json`** — 인메모리 `recent_messages` deque (`maxlen=500`) 의 60초 주기 atomic snapshot. dedup window + raw 로그 역할.
3. **`state.json`** — `current_model` 등. 모델 변경 시점에 즉시 저장.

데몬 크래시 시 잃을 수 있는 것:
- 마지막 60초간의 message dedup 정보 → 매우 드물게 더블 액션 가능
- 진행 중이던 AI 호출 → 그냥 사라짐 (재시도 안 함)
- TDLib 가 확인 못한 in-flight send → TDLib binlog 에서 복구

이 정도 손실은 personal 데몬에서는 수용 가능한 수준이라 SQLite + replay 같은 무거운 영속성 안 씀.

---

## 로그 형식

```
[init] model=gpt-5.4
[init] OPENAI_API_KEY not set — gpt-* models will fail   ← 키 없을 때만
[load] 27 recent messages restored
[auth] ready
[me] 친구챗 | '오늘 날씨 좋다 to en'  [translate: 'to en']
[me] -> (translate) 'The weather is nice today.'
[me] 가족톡 | '테스트'  [reply: '테스트']
[me] -> (reply) 'test done'
[me] 친구챗 | '안녕하세요'                                 ← 매칭 안 된 메시지
[config] reloaded                                         ← config.json 수정 시
[me] 친구챗 | 'ai model to gemini-2.5-pro'  [set_model: 'ai model to ']
[me] -> (set_model) '[ok] model -> gemini-2.5-pro (gemini)'
[exit] cleanup complete
```

| prefix | 의미 |
|---|---|
| `[init]` | 부트 시 상태 (모델, API 키 체크) |
| `[auth]` | 인증 상태 전환 |
| `[load]` | `messages.json` 에서 recent_messages 복원 |
| `[save]` | 스냅샷 저장 실패 시 |
| `[state]` | `state.json` 로드/저장 실패 시 |
| `[config]` | hot reload |
| `[me]` | 사용자 본인 메시지 + 매칭 결과 + 액션 결과 |
| `[debug]` | (`debug: true` 일 때만) raw TDLib update preview |
| `[error]` | action / send 실패 |
| `[exit]` | 종료 시 cleanup |

TDLib 자체의 stderr 로그 (`[ 1][t 4][...]` 같은 거) 는 `setLogVerbosityLevel: 0` 으로 차단해둠.

---

## 트러블슈팅

### `OSError: <path>: cannot open shared object file`

`config.json` 의 `tdjson_path` 가 잘못됨. `libtdjson.so` 의 실제 위치 확인:
```bash
find / -name 'libtdjson.so' 2>/dev/null
```

### `[init] OPENAI_API_KEY not set — gpt-* models will fail`

`.env` 에 `OPENAI_API_KEY` 가 비어있음. 수정 후 데몬 재시작.

### `[error] unknown model: gpt-5.4` (set_model 에서)

해당 모델이 `config.json` 의 `ai.models` 에 등록되지 않음. 추가하면 hot reload 됨.

### `[openai 401] {"error": ...}`

API 키 invalid. `.env` 확인 후 재시작.

### `[gemini 400] ...`

대부분 모델 ID 가 틀렸거나 API 키가 잘못된 경우. 모델 ID 는 https://ai.google.dev/gemini-api/docs/models 에서 확인.

### 인증 코드 재요청 / 세션 리셋

`tdlib/` 디렉토리 통째로 삭제하면 다음 실행 시 첫 auth 부터 다시 시작:
```bash
rm -rf tdlib/
python main.py
```

### `[auth] tdlib closed; exiting`

TDLib 가 인증 상태에서 `closed` 로 떨어진 상황. 보통은 잘못된 코드 입력이나 phone number 형식 오류. `tdlib/` 삭제 후 재시작.

### 두 번 답장하는 것 같음

데몬을 두 개 동시에 띄우고 있는지 확인. 같은 user account 에 두 데몬이 붙어 있으면 둘 다 같은 `updateNewMessage` 를 받아서 둘 다 답장함.

```bash
ps aux | grep main.py
```

### 메시지를 못 보는 것 같음

1. 데몬이 그 채팅방을 알고 있는지 확인 — 데몬은 시작 시 `loadChats` 로 main chat list 를 로드함. archived chat 이나 새 채팅방은 자동으로 안 들어올 수 있음.
2. `debug: true` 로 켜고 raw update 가 들어오는지 확인.
3. 다른 디바이스에서 보낸 거 맞는지 확인 — 데몬이 띄워진 그 TDLib 인스턴스 자체에서 보낸 메시지는 `sending_state` 때문에 필터링됨.

---

## 의도적으로 안 한 것

- **SQLite / DB / replay 로직**: personal 데몬에 과함. 인메모리 + JSON 스냅샷이 충분.
- **OpenAI/Gemini SDK**: `httpx` 가 venv 에 이미 있고 직접 HTTP 호출이 더 가벼움. SDK 한 번 업그레이드하면 망가지는 종속성 없앰.
- **python-dotenv**: 10줄짜리 stdlib 파서로 충분.
- **AI 응답을 messages.json 에 저장**: AI 결과는 채팅에 이미 남아 있어서 저장 의미 없음. 원본 메시지만 저장.
- **요청 큐 / rate limit**: 분당 수십 번씩 트리거할 일 없는 personal 용도라 불필요.
- **메시지 편집 (`editMessageText`)**: `to en` 같은 트리거 suffix 가 채팅에 그대로 보이는 게 어색하긴 한데, 일단 단순함 우선. 나중에 옵션으로 추가 가능.
- **TDLib 의 `use_message_database`**: 디스크 사용량 큼. 단기 catch-up 은 binlog 만으로 충분.

---

## 참고

- TDLib 빌드 가이드: https://tdlib.github.io/td/build.html
- TDLib API 문서 (TL schema): https://core.telegram.org/tdlib/docs/
- OpenAI API 문서: https://platform.openai.com/docs/api-reference
- Gemini API 문서: https://ai.google.dev/gemini-api/docs
- `~/codes/na_log_bot/na_log_bot.py` — 같은 td_json ctypes 패턴의 reference (단, 이쪽은 polling 기반).
- `~/codes/gpt_cli/gptcli.py` — OpenRouter + openai SDK 를 쓰는 CLI 클라이언트 (이 데몬과는 패턴 다름).
