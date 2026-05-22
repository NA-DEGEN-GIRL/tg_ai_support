# tg_self_reply

This repo is a single-file Python Telegram user-account daemon.

## Current Architecture

- `main.py` uses TDLib (`libtdjson.so`) through `ctypes`.
- It processes outgoing Telegram messages from the authenticated user account.
- Static replies, translation, and LLM dispatch are configured through
  `config.json`.
- There are no OpenAI/Gemini API key calls in Python. LLM work is delegated to
  authenticated local CLIs.

## LLM Bridge

- Translation uses `codex exec` headless and writes the final answer to
  `.llm_bridge/requests/<id>/output.md`.
- General LLM requests use tmux interactive sessions:
  - `to ai` and `to codex` -> Codex
  - `to claude` -> Claude
  - `to grok` -> Grok
- Each request creates:
  - `input.md`
  - `output.md`
  - `meta.json`
- The daemon gives the CLI a minimal instruction: read `input.md` and write only
  the final answer to `output.md`.
- Non-`cont` requests recreate the provider tmux session to clear context.
- Requests ending in `cont` reuse the existing provider session.
- Provider tmux commands run with bypass/always-approve settings so bridge file
  writes do not block on terminal approval prompts.

## Runtime State

- `tdlib/`: Telegram auth/session database, gitignored.
- `messages.json`: recent outgoing message dedup snapshot, gitignored.
- `state.json`: current default provider, gitignored.
- `.llm_bridge/`: LLM request files, gitignored.

## Notes For Changes

- Preserve TDLib auth and outgoing-message filtering.
- Keep `sending_state` loop prevention.
- Do not add HTTP AI SDK dependencies unless the architecture changes again.
- If changing triggers, update both `config.json.example` and README.
