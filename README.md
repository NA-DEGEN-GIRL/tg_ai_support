# tg_self_reply

내 Telegram 계정이 보낸 메시지를 감지해서, 같은 계정으로 자동 답장하는 개인용 데몬입니다.

봇 계정이 아니라 **내 user account 자체**를 TDLib로 로그인해서 사용합니다. 받는 사람 입장에서는 봇이 답하는 것이 아니라 내가 답장한 것처럼 보입니다.

## 핵심 기능

| 트리거 | 동작 |
| --- | --- |
| `테스트` 같은 정적 룰 | `config.json`에 등록된 문구로 바로 답장 |
| `문장 to en` / `to jp` / `to cn` / `to kr` | Codex CLI headless 모드로 번역 |
| `질문 to ai` / `질문 to codex` | Codex CLI로 답변 |
| `질문 to claude` | Claude CLI로 답변 |
| `질문 to grok` | Grok CLI로 답변 |
| `질문 to codex cont` | 직전 Codex 대화에 이어서 질문 |
| `ai provider to claude` | 기본 provider 변경 |

OpenAI/Gemini API 키는 필요 없습니다. 이미 로컬에서 로그인된 `codex`, `claude`, `grok` CLI 계정을 사용합니다.

## 사용 예시

내가 Telegram에서 이렇게 보내면:

```text
오늘 회의가 30분 늦어진다고 영어로 번역해줘 to en
```

데몬이 번역 결과를 같은 메시지에 reply로 보냅니다.

상대 메시지에 reply로 이렇게 보내면:

```text
to kr
```

데몬이 reply 대상 원문을 가져와서 한국어로 번역합니다.

LLM에게 물어볼 때는:

```text
오늘 일론 머스크가 올린 트윗 있으면 알려줘 to grok
```

검색이나 MCP가 필요하면 각 CLI가 자체 도구를 사용합니다. Python 코드에서 별도 API 키를 호출하지 않습니다.

## 문맥 정책

기본적으로 모든 LLM 요청은 **단발성 질문**으로 처리합니다. 이전 Telegram 질문의 문맥이 섞이지 않게 하기 위해, `cont`가 없으면 provider 세션을 새로 시작합니다.

이어 묻고 싶을 때만 `cont`를 붙입니다.

```text
방금 답변을 더 짧게 요약해줘 to claude cont
```

## 설치와 첫 실행

필요한 것:

- Python 3.10+
- `tmux`
- TDLib `libtdjson.so`
- 로그인 완료된 `codex`, `claude`, `grok` CLI

설정 파일을 만듭니다.

```bash
cp config.json.example config.json
$EDITOR config.json
```

채워야 하는 값:

- `tdjson_path`
- `api_id`
- `api_hash`

`api_id`와 `api_hash`는 https://my.telegram.org 에서 발급합니다.

첫 실행은 Telegram 인증 때문에 직접 실행하는 편이 좋습니다.

```bash
python main.py
```

전화번호, 로그인 코드, 2FA를 통과해서 `[auth] ready`가 나오면 됩니다.

이후부터는 tmux로 바로 실행합니다.

```bash
bash tmux.command
```

로그를 보려면:

```bash
tmux attach -t tgself
```

tmux에서 빠져나오려면 `Ctrl+B` 다음 `D`를 누릅니다.

## 내부 동작

번역은 `codex exec`로 한 번 실행하고 종료합니다.

일반 LLM 요청은 provider별 tmux 세션을 사용합니다. 데몬은 요청마다 임시 작업 파일을 만들고, CLI가 최종 답변만 `output.md`에 쓰면 그 내용을 Telegram으로 보냅니다.

Telegram에는 내부 tmux 세션명, 파일 경로, 작업 상태 메시지를 보내지 않습니다. 실패해도 사용자에게는 일반 에러 문구만 보냅니다.

## 중요한 파일

- `main.py`: TDLib 이벤트 루프, 룰 매칭, CLI 브릿지.
- `config.json`: 실제 런타임 설정. git에 올라가지 않습니다.
- `.llm_bridge/`: LLM 요청/응답 임시 파일. git에 올라가지 않습니다.
- `tdlib/`: Telegram 로그인 세션. git에 올라가지 않습니다.
- `messages.json`: 최근 처리 메시지 기록. git에 올라가지 않습니다.
- `state.json`: 현재 기본 provider 상태. git에 올라가지 않습니다.

## 보안 주의

`tdlib/`는 Telegram 계정 로그인 세션입니다. 이 디렉토리를 가진 사람은 내 Telegram 계정 권한을 사실상 사용할 수 있습니다. 절대 커밋, 업로드, 공유하지 마세요.

`config.json`에는 Telegram API credential이 들어갑니다. 이것도 git에 올라가지 않게 유지해야 합니다.

LLM provider 세션은 승인 프롬프트 없이 동작하도록 설정되어 있습니다. 그래야 Telegram 요청이 파일 쓰기 승인에서 멈추지 않습니다. 대신 Telegram 트리거는 로컬의 강한 권한을 가진 agent에게 명령을 보내는 것과 같으므로, 신뢰할 수 있는 개인 계정/환경에서만 사용하세요.

## 문제 해결

LLM 답변이 안 오면 먼저 메인 로그를 봅니다.

```bash
tmux attach -t tgself
```

특정 provider가 오래 걸리면 해당 provider가 검색이나 도구 실행 중일 수 있습니다. provider tmux 세션은 요청이 들어올 때 자동으로 생성됩니다.

설정을 바꾼 뒤에는 재시작합니다.

```bash
bash tmux.command
```
