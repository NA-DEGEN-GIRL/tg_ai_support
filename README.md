# tg_self_reply

> **내 텔레그램 계정에 자동 답장 / 번역 / AI 답변 기능을 붙여주는 개인 비서 데몬.**
> 봇 계정이 아니라 **내 계정 자체** 가 답장을 보내기 때문에, 받는 사람 입장에선 그냥 내가 직접 답장한 것처럼 보임.

---

## 이 데몬, 뭐 하는 거?

서버에서 백그라운드로 돌고 있다가 내가 핸드폰/PC 등 다른 디바이스에서 텔레그램에 메시지를 쓰면, 그 메시지를 보고 미리 정해둔 키워드 룰에 따라 자동으로 액션을 수행함. 내 계정으로 답장이 나가기 때문에 채팅방에 있는 사람들 입장에선 그냥 내가 답한 것처럼 보임 (봇 마크 같은 거 안 붙음).

지원 액션 4가지 + 보조 기능 1개:

### 1. 정적 자동 답장 (reply)

`config.json` 에 등록한 키워드를 보내면 정해둔 텍스트로 바로 답장.

```
나       : 테스트
나(자동) : test done
```

자주 쓰는 짧은 답변을 자동화하는 용도. 예: "ㅇㅋ" → "확인했습니다, 곧 처리하겠습니다".

### 2. 자동 번역 (translate)

내가 쓴 글이든 상대가 보낸 글이든 같은 키워드 (`to en` / `to jp` / `to cn` / `to kr`) 로 번역. 두 가지 컨텍스트로 동작:

**(a) 내가 쓴 글 번역** — 메시지 끝에 트리거 붙이면 그 메시지를 번역해서 답장:

```
나       : 오늘 회의 30분 정도 늦어질 것 같아요 to en
나(자동) : I think today's meeting will be delayed by about 30 minutes.
```

**(b) 상대 메시지 번역** — 상대 메시지에 reply 로 **트리거 한 줄만** 보내면 그 원본을 번역:

```
친구       : お疲れ様です。明日の打ち合わせは予定通りです。
나 (reply) : to kr
나(자동)   : 수고하셨습니다. 내일 미팅은 예정대로 진행됩니다.
```

데몬이 내가 reply 한 메시지의 원본을 TDLib 의 `getMessage` 로 fetch 한 다음 AI 에 번역 요청 보냄.

> 주의: reply 메시지에 **내가 쓴 글 + 트리거** 를 같이 보내면 (`내 문장 to en` 처럼) 원본 메시지가 아니라 **내가 쓴 글** 이 번역됨. 답장 컨텍스트 안에서 내가 직접 작성한 다른 언어 메시지를 번역하고 싶을 때 유용.

### 3. AI 빠른 질문 (ask_ai)

문장 끝에 `to ai` 를 붙이면 AI 한테 그 내용을 물어봐서 답변:

```
나       : 파이썬에서 dict 를 value 기준으로 정렬하는 한 줄 to ai
나(자동) : sorted_d = dict(sorted(d.items(), key=lambda x: x[1]))
```

상대 메시지에 reply 로 `to ai` 만 보내면 그 메시지를 AI 한테 물어봐줌. 추가 instruction 도 가능:

```
친구       : What's the weather forecast for Seoul tomorrow?
나 (reply) : 한국어로 짧게 to ai
나(자동)   : 내일 서울은 흐리고 일부 비가 올 가능성이 있습니다.
```

### 4. AI 모델 변경 (set_model)

`config.json` 의 `ai.models` 에 등록된 모델 사이에서 즉석 전환:

```
나       : ai model to gemini-2.5-pro
나(자동) : [ok] model -> gemini-2.5-pro (gemini)
```

기본 등록된 모델: `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.4-pro`, `gpt-5.3-codex`, `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-3-flash-preview` 등. 변경된 모델은 `state.json` 에 저장돼서 데몬 재시작해도 유지됨.

### + 마크다운 자동 변환 (보조 기능)

AI 답변에 standard markdown (`**굵게**`, `*기울임*`, `` `코드` ``, ```` ```code block``` ````, `[링크](url)`, `# 헤더`, `- 리스트`) 이 섞여 있어도 텔레그램 포맷으로 자동 변환됨. AI 가 뱉는 raw 마크다운이 채팅에 별표 그대로 보이는 일 없음.

---

## 잠깐, "봇" 아니라고?

텔레그램에는 두 종류의 클라이언트가 있음:

|            | 봇 (Bot) 계정          | User 계정 (= 사람 계정)      |
|------------|------------------------|------------------------------|
| 만드는 법  | `@BotFather` 에 등록   | 핸드폰 번호로 가입            |
| 표시       | 이름 옆에 봇 마크      | 일반 사용자                   |
| 권한 범위  | 받는 메시지만 처리 가능 | 텔레그램 모든 기능            |
| 사용 API   | Bot API (간단한 HTTP)   | MTProto / TDLib (저수준)      |

이 프로젝트는 **봇 계정이 아니라 user 계정 (= 내 계정 자체)** 으로 동작함. 그래서:

- 받는 사람 입장에선 그냥 내가 직접 답한 것처럼 보임 (봇 마크 X)
- 봇이 초대 안 된 채팅방에서도 동작 (1:1, 일반 그룹, 채널 등 어디든)
- **내가 보낸 메시지를 자동으로 후처리 가능** ← Bot API 로는 절대 못 하는 거

이걸 가능하게 하려면 텔레그램이 제공하는 **TDLib** (`libtdjson.so`) 라는 C++ 라이브러리가 필요함. 이 프로젝트는 그걸 Python 의 `ctypes` 로 직접 바인딩해서 사용함.

> ⚠️ **중요한 보안 사항**: TDLib 세션 파일 (`tdlib/db.sqlite`, `tdlib/td.binlog`) 은 **내 텔레그램 계정과 동등한 권한** 을 가짐. 이 파일을 가진 사람은 내 계정으로 로그인되어 있는 것과 같음. 절대 git 에 커밋하거나 공유 / 백업 / 업로드 하면 안 됨. 이 프로젝트의 `.gitignore` 가 자동으로 차단해두긴 했지만, 별도 백업을 만들 때도 주의할 것. `.env` (API 키) 와 `config.json` (api_id/api_hash) 도 마찬가지.

---

## 빠른 시작 (TL;DR)

이미 TDLib 빌드 + Python 3.10+ + uv 가 준비된 사람용 한 줄 정리:

```bash
git clone https://github.com/NA-DEGEN-GIRL/tg_ai_support.git tg_self_reply
cd tg_self_reply

# venv + 의존성
uv venv .venv
source .venv/bin/activate
uv pip compile requirements.in -o requirements.txt
uv pip sync requirements.txt

# 설정 파일 복사 후 채우기
cp config.json.example config.json
cp .env.example .env
$EDITOR config.json    # api_id / api_hash 채우기 (https://my.telegram.org)
$EDITOR .env           # OPENAI_API_KEY 채우기 (https://platform.openai.com/api-keys)

# 첫 실행 (interactive: phone, code, 2FA)
python main.py
```

`[auth] ready` 가 뜨면 성공. 이후로는 백그라운드로:

```bash
bash tmux.command            # detached tmux 세션 시작
tmux attach -t tgself        # 로그 보기
```

처음 셋업하는 사람은 아래 [사전 준비](#사전-준비) → [설치](#설치) → [사용법](#사용법) 순서로 보면 됨.

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

### 7. tmux 로 백그라운드 실행 (선택)

첫 인증 후에는 `tmux.command` 로 detached tmux 세션에 띄울 수 있음:

```bash
bash tmux.command            # 세션 시작
tmux attach -t tgself        # 로그 보기 (Ctrl+B 다음 D 로 detach)
tmux kill-session -t tgself  # 종료
```

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

같은 룰. 다른 사용 방식. **트리거만 단독으로** reply 해야 원본이 번역됨:

```
친구: How's it going?
나 (그 메시지에 reply 로): to kr
나 (데몬): 잘 지내?   ← reply (내 to kr 메시지에 답장)
```

처리 과정:
1. 내 outgoing 메시지의 `reply_to.@type` 가 `messageReplyToMessage` 인지 확인
2. 맞으면 → TDLib 의 `getMessage` 로 원본 메시지 fetch
3. 그 텍스트를 번역해서 내 reply 메시지에 reply

#### Case 3: reply 안에서 내 글 번역

reply 메시지에 **내가 쓴 글 + 트리거** 를 같이 보내면 원본이 아니라 **내가 쓴 글** 이 번역됨:

```
친구: How's it going?
나 (reply): 잘 지내고 있어요 to en
나 (데몬): I'm doing well.   ← 친구 메시지가 아니라 내 글이 번역됨
```

원칙:
- reply + 트리거만 → 원본 번역 (Case 2)
- reply + 내 글 + 트리거 → 내 글 번역 (Case 3)
- 그냥 메시지 + 트리거 → 내 글 번역 (Case 1)

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

## 자주 묻는 질문 (FAQ)

### Q. 내가 핸드폰에서 보낸 메시지에만 반응하나요? 데스크탑에서 보내도 되나요?

같은 텔레그램 계정으로 로그인된 어떤 디바이스에서 보내든 다 반응함. 데몬은 TDLib 가 emit 하는 `updateNewMessage` 의 `is_outgoing == True` 만 체크하기 때문에, 본인 계정에서 나간 메시지면 출처 디바이스 상관없음.

단, **데몬 자체가 떠 있는 그 TDLib 인스턴스에서 직접 보낸 메시지** 는 `sending_state` 필터로 제외됨 (loop 방지). 그래서 데몬이 답장으로 보낸 메시지가 다시 룰에 매칭돼서 무한 루프 도는 일은 없음.

### Q. 답장이 좀 느린데요? AI 호출 끝날 때까지 기다리나요?

`reply` (정적 텍스트) 답장은 거의 즉시 (< 100ms). `translate` / `ask_ai` 는 AI provider 응답 속도에 따라 다름:

- OpenAI `gpt-5.4-mini` / `nano`: 1~3초
- OpenAI `gpt-5.4`: 3~10초
- Gemini `2.5-flash`: 1~3초
- Gemini `3-flash-preview`: 1~5초
- 무거운 reasoning 모델 (`gpt-5.4-pro`, `gpt-5-pro`): 10~30초

답장 늦는 동안 데몬은 다른 메시지를 동시에 처리할 수 있음 (`asyncio.create_task` 로 spawn 되기 때문에 이벤트 루프 안 막힘).

### Q. 친구가 내 답장이 자동인 거 알아챌 수 있나요?

봇 마크 같은 시각적 표시는 없음. 다만:

- 답장 속도가 일관되게 짧으면 (특히 정적 답장의 경우) 의심받을 수 있음.
- 트리거 키워드 (`to en` 등) 가 채팅에 그대로 보임. 데몬은 원본 메시지를 편집하지 않음. 어색하면 트리거 메시지를 보낸 후 텔레그램 클라이언트에서 직접 삭제하면 됨.

### Q. 데몬을 두 개 띄우면 어떻게 되나요?

같은 user account 로 두 데몬이 떠 있으면 둘 다 같은 `updateNewMessage` 를 받기 때문에 답장이 두 번 나감. 한 번에 한 인스턴스만 띄울 것:

```bash
ps aux | grep main.py
tmux ls
```

### Q. 룰을 추가했는데 적용이 안 돼요.

`config.json` 의 mtime 이 바뀌어야 hot reload 가 트리거됨. 에디터에 따라 저장해도 mtime 이 안 바뀌거나 swap 파일로 저장 후 rename 하는 경우 등이 있음. 안 되면:

1. `touch config.json` 으로 강제로 mtime 갱신
2. 데몬 로그에 `[config] reloaded` 가 뜨는지 확인

`api_id`, `api_hash`, `tdjson_path`, `max_recent_messages`, `save_interval_seconds` 같은 부트 시 한 번만 읽는 필드는 hot reload 안 됨. 재시작 필요.

### Q. AI API 비용이 얼마나 나오나요?

AI provider 의 사용량 기반 과금. 텔레그램 메시지 1개당 prompt 길이에 따라 다르지만 대략적인 감 (실제 가격은 provider 가격표 참고):

- `gpt-5.4-mini` 짧은 번역 1회: 매우 저렴 (1만 번 = 약 $1 수준)
- `gpt-5.4` 짧은 번역 1회: 적당함 (1천 번 = 약 $1 수준)
- `gpt-5.4-pro`, `gpt-5-pro` 같은 reasoning 모델: 일반 모델보다 5~10배 비쌈

폭주 방지를 위해 OpenAI / Gemini 콘솔에서 monthly hard limit 을 미리 설정해두는 걸 권장.

### Q. 메시지를 못 보는 것 같아요.

1. **데몬이 그 채팅방을 알고 있는지**: 데몬은 부트 시 `loadChats` 로 main chat list 만 로드함. archived chat / folder 안의 chat / 새로 추가된 채팅방은 자동 로드 안 될 수 있음. 한 번 핸드폰 클라이언트에서 그 채팅방을 열면 동기화됨.
2. **debug 모드 켜기**: `config.json` 에서 `"debug": true` → 모든 raw update 가 `[debug]` 로 보임. (단, 양이 많아서 일시적 디버깅 용도로만)
3. **데몬 자체 디바이스 vs 다른 디바이스**: 데몬이 떠 있는 그 머신의 TDLib 인스턴스에서 직접 보낸 메시지는 무시됨 (loop 방지). 핸드폰 등 다른 디바이스에서 보내야 함.

### Q. 인증을 다시 하고 싶어요 / 세션이 깨졌어요.

```bash
rm -rf tdlib/
python main.py
```

`tdlib/` 디렉토리를 통째로 삭제하면 세션이 리셋되고 다음 실행 시 phone/code/2FA 부터 다시 받음. **단, 이건 내 계정의 디바이스 세션 하나를 무효화하는 거라 핸드폰 텔레그램에서 "Active Sessions" 에 다시 새로 뜸. 정상.**

### Q. 데몬이 답장한 결과는 어디서 볼 수 있나요?

세 군데:

1. **터미널 로그** — `[me] -> (action) '결과'` 라인. tmux 안에서 `tmux attach -t tgself` 로 볼 수 있음.
2. **텔레그램 채팅방 자체** — 답장이 실제로 거기 발송됨.
3. **`messages.json`** — 60초마다 스냅샷되는 인메모리 deque 의 최근 500개. 단, 이건 원본 메시지만 저장하고 답장 결과는 안 저장 (채팅방에 이미 남아 있어서 중복 저장 불필요).

### Q. 내 계정이 텔레그램에서 막힐 수도 있나요?

자동화된 메시지를 너무 많이 보내면 텔레그램에서 임시 / 영구 ban 위험이 있음. 특히:

- 같은 메시지를 짧은 시간에 다른 사람들한테 spam 으로 보내는 경우
- 가입한 지 얼마 안 된 새 계정이 갑자기 자동화하는 경우

개인 용도 (자기 자신의 메시지에 답장 / 친구들과의 대화에 가끔 트리거) 정도면 보통 문제 없음. 절대 안전 보장은 못 함. 본인 책임으로 사용.

### Q. config.json 에 새 번역 언어를 추가하고 싶어요.

`rules` 배열에 같은 형식으로 추가하면 됨. 저장만 하면 hot reload:

```json
{"match": "suffix", "keyword": "to es", "action": "translate", "lang": "Spanish"},
{"match": "suffix", "keyword": "to fr", "action": "translate", "lang": "French"},
{"match": "suffix", "keyword": "to vi", "action": "translate", "lang": "Vietnamese"}
```

`lang` 은 AI 한테 전달되는 번역 대상 언어 이름이라 자연어로 적으면 됨.

### Q. 새 AI 모델이 나왔는데 추가하려면?

`config.json` 의 `ai.models` 의 해당 provider 배열에 모델 ID 추가 → 저장 → hot reload 됨. 그 다음 `ai model to <new>` 명령으로 전환 가능.

```json
"models": {
    "openai": ["gpt-5.4", "gpt-5.5", "..."],
    "gemini": ["gemini-2.5-pro", "gemini-3.1-pro", "..."]
}
```

### Q. tmux 가 뭔가요? 꼭 써야 하나요?

tmux 는 터미널 멀티플렉서. 데몬을 돌리다가 SSH 연결을 끊어도 백그라운드에서 계속 동작하게 해줌. 안 쓰면 SSH 끊는 순간 데몬도 같이 죽음.

대안:
- `nohup python main.py &` (간단하지만 로그 보기 불편)
- `systemd` service (제대로 된 서비스 운영, 셋업 필요)
- `screen` (tmux 와 유사한 옛날 도구)

이 프로젝트는 단순함을 위해 `tmux.command` helper 만 제공. 더 진지하게 운영하려면 systemd 권장.

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
