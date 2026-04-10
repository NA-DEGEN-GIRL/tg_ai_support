"""
tg_self_reply: Telegram self-reply daemon with AI features.

Listens for messages your own Telegram user account sends and dispatches the
configured action when text matches a rule:

  - reply     : send a static text reply
  - translate : ask the current AI model to translate (target lang from rule)
  - ask_ai    : send the message to the current AI model and reply with the answer
  - set_model : switch the active AI model (e.g. "ai model to gpt-5.4")

For translate / ask_ai, when the trigger comes from a *reply* to someone else's
message, the source text is fetched from that original message via TDLib's
getMessage instead of taken from your own text.

config.json hot-reloads on mtime change so adding rules or models doesn't
require a restart. API keys live in .env (OPENAI_API_KEY, GEMINI_API_KEY).
"""

import asyncio
import ctypes
import html as html_module
import json
import os
import re
import sys
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"
MESSAGES_FILE = ROOT / "messages.json"
STATE_FILE = ROOT / "state.json"


# ----- .env loader (stdlib only) -----
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


load_env(ENV_PATH)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


# ----- Config (hot reload on mtime) -----
_config_cache: dict = {}
_config_mtime: float = 0.0


def get_config() -> dict:
    """Reparse config.json when its mtime changes; return the cached dict otherwise."""
    global _config_cache, _config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return _config_cache
    if mtime == _config_mtime and _config_cache:
        return _config_cache
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            new_config = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[config] reload failed: {e}")
        return _config_cache
    if _config_mtime != 0:
        print("[config] reloaded")
    _config_cache = new_config
    _config_mtime = mtime
    return _config_cache


# Bootstrap fields that don't hot-reload (changing them needs a restart anyway).
get_config()
API_ID = int(_config_cache["api_id"])
API_HASH = _config_cache["api_hash"]
TDJSON_PATH = os.path.expanduser(_config_cache["tdjson_path"])
MAX_RECENT = int(_config_cache.get("max_recent_messages", 500))
SAVE_INTERVAL = float(_config_cache.get("save_interval_seconds", 60))
DB_DIR = str(ROOT / "tdlib")


def get_rules() -> list:
    return get_config().get("rules", [])


def get_ai_section() -> dict:
    return get_config().get("ai", {})


def get_default_model() -> str:
    return get_ai_section().get("default_model", "")


def get_provider(model: str) -> Optional[str]:
    """Look up which provider serves a given model name."""
    for provider, models in get_ai_section().get("models", {}).items():
        if model in models:
            return provider
    return None


# ----- Runtime state -----
recent_messages: "deque[dict]" = deque(maxlen=MAX_RECENT)
chat_titles: dict = {}
authorized = False
dirty = False
_state: dict = {}


def load_state() -> None:
    global _state
    if not STATE_FILE.exists():
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            _state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state] load failed: {e}")


def save_state() -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def get_current_model() -> str:
    return _state.get("current_model") or get_default_model()


def set_current_model(model: str) -> None:
    _state["current_model"] = model
    try:
        save_state()
    except OSError as e:
        print(f"[state] save failed: {e}")


# ----- TDLib JSON binding -----
_td = ctypes.CDLL(TDJSON_PATH)
_td.td_json_client_create.restype = ctypes.c_void_p
_td.td_json_client_send.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_td.td_json_client_receive.argtypes = [ctypes.c_void_p, ctypes.c_double]
_td.td_json_client_receive.restype = ctypes.c_char_p
_td.td_json_client_execute.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_td.td_json_client_execute.restype = ctypes.c_char_p
_td.td_json_client_destroy.argtypes = [ctypes.c_void_p]

_client = _td.td_json_client_create()
_recv_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tdrecv")


def td_send(query: dict) -> None:
    _td.td_json_client_send(_client, json.dumps(query).encode("utf-8"))


def td_receive(timeout: float = 1.0):
    raw = _td.td_json_client_receive(_client, timeout)
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def td_execute(query: dict) -> Optional[dict]:
    """Synchronous TDLib call. Used for parseTextEntities (markdown/HTML formatting)."""
    raw = _td.td_json_client_execute(_client, json.dumps(query).encode("utf-8"))
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None


# ----- TDLib request/response correlation -----
_pending_requests: dict = {}


async def td_request(query: dict, timeout: float = 10.0) -> Optional[dict]:
    """Send a request and await the response with matching @extra."""
    req_id = uuid.uuid4().hex
    query["@extra"] = req_id
    fut = asyncio.get_running_loop().create_future()
    _pending_requests[req_id] = fut
    td_send(query)
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _pending_requests.pop(req_id, None)


# ----- Recent message buffer -----
def already_seen(chat_id: int, message_id: int) -> bool:
    """Linear scan; cheap because the buffer is capped small."""
    for m in recent_messages:
        if m["chat_id"] == chat_id and m["message_id"] == message_id:
            return True
    return False


def remember(record: dict) -> None:
    global dirty
    recent_messages.append(record)
    dirty = True


def load_messages() -> None:
    if not MESSAGES_FILE.exists():
        return
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[load] failed: {e}")
        return
    for record in data[-MAX_RECENT:]:
        recent_messages.append(record)
    print(f"[load] {len(recent_messages)} recent messages restored")


def save_messages() -> None:
    """Atomic snapshot via tmp file + rename."""
    tmp = MESSAGES_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(list(recent_messages), f, ensure_ascii=False, indent=2)
    os.replace(tmp, MESSAGES_FILE)


async def periodic_save() -> None:
    """Snapshot the buffer every SAVE_INTERVAL seconds, only when dirty."""
    global dirty
    while True:
        await asyncio.sleep(SAVE_INTERVAL)
        if not dirty:
            continue
        try:
            save_messages()
            dirty = False
        except OSError as e:
            print(f"[save] failed: {e}")


# ----- Rule matching -----
def match_rule(text: str) -> Optional[dict]:
    stripped = text.strip()
    lower = stripped.lower()
    for rule in get_rules():
        keyword = rule.get("keyword", "")
        if not keyword:
            continue
        match_type = rule.get("match", "exact")
        kw_lower = keyword.lower()
        if match_type == "exact" and stripped == keyword:
            return rule
        if match_type == "contains" and keyword in text:
            return rule
        if match_type == "prefix" and lower.startswith(kw_lower):
            return rule
        if match_type == "suffix" and lower.endswith(kw_lower):
            return rule
    return None


def strip_suffix(text: str, suffix: str) -> str:
    text = text.rstrip()
    if text.lower().endswith(suffix.lower()):
        return text[: -len(suffix)].rstrip()
    return text


def strip_prefix(text: str, prefix: str) -> str:
    if text.lower().startswith(prefix.lower()):
        return text[len(prefix):].lstrip()
    return text


# ----- Markdown → Telegram HTML converter -----

_PLACEHOLDER_RE = re.compile(r"\x00C(\d+)\x00")


def md_to_telegram_html(text: str) -> str:
    """Convert standard markdown (as AI models output) to Telegram-flavored HTML.

    Telegram HTML only supports a small subset of inline tags. We map:
        **bold** / __bold__         -> <b>
        *italic* / _italic_         -> <i>
        ~~strike~~                  -> <s>
        `inline code`               -> <code>
        ```lang\\ncode```           -> <pre><code class="language-lang">
        [text](url)                 -> <a href="url">
        # Header                    -> <b> (Telegram has no header tag)
        - / * bullet                -> • prefix (no list tag)

    Code blocks and link URLs are stashed first so the inline-formatting
    regexes can't mangle their contents. Bold/italic regexes use word-boundary
    lookarounds so identifiers (`foo_bar`, `2*x*5`) aren't accidentally matched.
    """
    segments: list = []

    def stash(m):
        segments.append(m.group(0))
        return f"\x00C{len(segments) - 1}\x00"

    # 1) Stash code blocks (raw content preserved through escaping)
    text = re.sub(r"```(?:[\w+-]*\n)?.*?```", stash, text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", stash, text)

    # 2) Stash markdown links as fully-formed anchor tags
    def stash_link(m):
        link_text = m.group(1)
        url = m.group(2)
        text_esc = html_module.escape(link_text, quote=False)
        url_esc = html_module.escape(url, quote=True)
        anchor = f'<a href="{url_esc}">{text_esc}</a>'
        segments.append(anchor)
        return f"\x00C{len(segments) - 1}\x00"

    text = re.sub(r"\[([^\[\]]+?)\]\(([^()\s]+?)\)", stash_link, text)

    # 3) HTML-escape the rest
    text = html_module.escape(text, quote=False)

    # 4) Headers (before bold, so `# **x**` still gets bolded inside)
    text = re.sub(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 5) Bold (process before italic so ** doesn't get half-eaten).
    # ASCII-only boundary class so Korean / Japanese / Chinese text adjacent to
    # ** still bolds (`안녕**중요**입니다`). Python's \w would match those as
    # word chars and block the match.
    text = re.sub(r"(?<![A-Za-z0-9_*])\*\*(.+?)\*\*(?![A-Za-z0-9_*])", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![A-Za-z0-9_])__(.+?)__(?![A-Za-z0-9_])", r"<b>\1</b>", text, flags=re.DOTALL)

    # 6) Italic — same ASCII-only boundary; still skips identifiers (foo_bar)
    # and math (2*x*5) but allows non-ASCII text adjacent to a single * or _.
    text = re.sub(r"(?<![A-Za-z0-9_*])\*(?!\*)(.+?)(?<!\*)\*(?![A-Za-z0-9_*])", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![A-Za-z0-9_])_(?!_)(.+?)(?<!_)_(?![A-Za-z0-9_])", r"<i>\1</i>", text, flags=re.DOTALL)

    # 7) Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)

    # 8) Bullets at start of line
    text = re.sub(r"^[ \t]*[-*][ \t]+", "• ", text, flags=re.MULTILINE)

    # 9) Restore stashed segments to their final HTML form
    def restore(m):
        idx = int(m.group(1))
        raw = segments[idx]
        if raw.startswith("<a "):
            return raw
        if raw.startswith("```"):
            inner = re.match(r"```([\w+-]*)\n?(.*?)```", raw, re.DOTALL)
            if inner:
                lang = inner.group(1)
                code = inner.group(2).rstrip("\n")
                code_esc = html_module.escape(code, quote=False)
                if lang:
                    return f'<pre><code class="language-{lang}">{code_esc}</code></pre>'
                return f"<pre>{code_esc}</pre>"
            return html_module.escape(raw, quote=False)
        # Inline `code`
        inner = raw[1:-1]
        return f"<code>{html_module.escape(inner, quote=False)}</code>"

    return _PLACEHOLDER_RE.sub(restore, text)


def format_text(text: str) -> dict:
    """Convert markdown text to a TDLib formattedText, falling back to plain on parse error."""
    if not text:
        return {"@type": "formattedText", "text": "(empty)"}
    html_text = md_to_telegram_html(text)
    result = td_execute({
        "@type": "parseTextEntities",
        "text": html_text,
        "parse_mode": {"@type": "textParseModeHTML"},
    })
    if result and result.get("@type") == "formattedText":
        return result
    err = result.get("message", "no response") if result else "no response"
    print(f"[fmt] HTML parse failed ({err}), falling back to plain text")
    return {"@type": "formattedText", "text": text}


# ----- Telegram actions -----
def send_reply(chat_id: int, message_id: int, text: str) -> None:
    formatted = format_text(text)
    td_send({
        "@type": "sendMessage",
        "chat_id": chat_id,
        "reply_to": {
            "@type": "inputMessageReplyToMessage",
            "message_id": message_id,
        },
        "input_message_content": {
            "@type": "inputMessageText",
            "text": formatted,
        },
    })


async def get_message_text(chat_id: int, message_id: int) -> str:
    """Fetch a message via TDLib and extract its text or caption."""
    msg = await td_request({
        "@type": "getMessage",
        "chat_id": chat_id,
        "message_id": message_id,
    }, timeout=5.0)
    if not msg or msg.get("@type") == "error":
        return ""
    content = msg.get("content", {})
    if content.get("@type") == "messageText":
        return content.get("text", {}).get("text", "")
    caption = content.get("caption")
    if caption and caption.get("@type") == "formattedText":
        return caption.get("text", "")
    return ""


def extract_reply_context(message: dict) -> Optional[dict]:
    """If `message` is itself a reply, return the (chat_id, message_id) it replies to."""
    reply_to = message.get("reply_to")
    if not reply_to or reply_to.get("@type") != "messageReplyToMessage":
        return None
    return {
        "chat_id": reply_to.get("chat_id") or message.get("chat_id"),
        "message_id": reply_to.get("message_id"),
    }


# ----- AI clients -----
TRANSLATE_PROMPT = (
    "Translate the following text to {lang}. "
    "Output only the translation — no quotes, no commentary, no explanations.\n\n"
    "Text:\n{text}"
)


async def call_openai(prompt: str, model: str) -> str:
    if not OPENAI_API_KEY:
        return "[error] OPENAI_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if r.status_code != 200:
            return f"[openai {r.status_code}] {r.text[:200]}"
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[openai error] {e}"


async def call_gemini(prompt: str, model: str) -> str:
    if not GEMINI_API_KEY:
        return "[error] GEMINI_API_KEY not set"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": GEMINI_API_KEY},
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
        if r.status_code != 200:
            return f"[gemini {r.status_code}] {r.text[:200]}"
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"[gemini error] {e}"


async def call_ai(prompt: str, model: Optional[str] = None) -> str:
    model = model or get_current_model()
    if not model:
        return "[error] no model selected"
    provider = get_provider(model)
    if provider == "openai":
        return await call_openai(prompt, model)
    if provider == "gemini":
        return await call_gemini(prompt, model)
    return f"[error] unknown model: {model}"


# ----- Action implementations -----
async def _do_translate(text: str, reply_ctx: Optional[dict], rule: dict) -> str:
    """Translate logic.

    - Reply context + trigger ONLY (e.g. just `to en`)  -> translate the original
      message we're replying to.
    - Reply context + my own text before the trigger    -> translate MY text
      (the user might be writing a translation of their own message even while
      replying to someone — don't yank the source from the reply target).
    - No reply context + my text                        -> translate my text.
    - No reply context + bare trigger                   -> error.
    """
    keyword = rule.get("keyword", "")
    target_lang = rule.get("lang", "English")
    user_part = strip_suffix(text, keyword)

    if user_part:
        source = user_part
    elif reply_ctx:
        source = await get_message_text(reply_ctx["chat_id"], reply_ctx["message_id"])
        if not source:
            return "[error] couldn't fetch original message"
    else:
        return "[error] empty source"

    prompt = TRANSLATE_PROMPT.format(lang=target_lang, text=source)
    return await call_ai(prompt)


async def _do_ask_ai(text: str, reply_ctx: Optional[dict], rule: dict) -> str:
    keyword = rule.get("keyword", "")
    user_part = strip_suffix(text, keyword)
    if reply_ctx:
        source = await get_message_text(reply_ctx["chat_id"], reply_ctx["message_id"])
        if not source:
            return "[error] couldn't fetch original message"
        prompt = f"{user_part}\n\n---\n{source}" if user_part else source
    else:
        if not user_part:
            return "[error] empty prompt"
        prompt = user_part
    return await call_ai(prompt)


def _do_set_model(text: str, rule: dict) -> str:
    keyword = rule.get("keyword", "")
    new_model = strip_prefix(text, keyword).strip()
    if not new_model:
        return "[error] no model name"
    provider = get_provider(new_model)
    if provider is None:
        return f"[error] unknown model: {new_model}"
    set_current_model(new_model)
    return f"[ok] model -> {new_model} ({provider})"


async def dispatch_action(text: str, chat_id: int, message_id: int,
                           reply_ctx: Optional[dict], rule: dict) -> None:
    action = rule.get("action", "reply")
    try:
        if action == "reply":
            result = rule.get("text", "")
        elif action == "translate":
            result = await _do_translate(text, reply_ctx, rule)
        elif action == "ask_ai":
            result = await _do_ask_ai(text, reply_ctx, rule)
        elif action == "set_model":
            result = _do_set_model(text, rule)
        else:
            result = f"[error] unknown action: {action}"
    except Exception as e:
        result = f"[error] {e}"

    try:
        send_reply(chat_id, message_id, result)
    except Exception as e:
        print(f"[error] send_reply failed: {e}")
        return

    log_action_done(action, result)


# ----- Auth -----
def handle_authorization_state(auth_state: dict) -> bool:
    state_type = auth_state["@type"]
    if state_type == "authorizationStateWaitTdlibParameters":
        td_send({
            "@type": "setTdlibParameters",
            "use_test_dc": False,
            "database_directory": DB_DIR,
            "use_file_database": False,
            "use_chat_info_database": True,
            "use_message_database": False,
            "use_secret_chats": False,
            "api_id": API_ID,
            "api_hash": API_HASH,
            "system_language_code": "en",
            "device_model": "Desktop",
            "application_version": "1.0",
        })
    elif state_type == "authorizationStateWaitPhoneNumber":
        phone = input("Phone number (with country code, e.g. +821012345678): ").strip()
        td_send({"@type": "setAuthenticationPhoneNumber", "phone_number": phone})
    elif state_type == "authorizationStateWaitCode":
        code = input("Login code: ").strip()
        td_send({"@type": "checkAuthenticationCode", "code": code})
    elif state_type == "authorizationStateWaitPassword":
        password = getpass("2FA password: ")
        td_send({"@type": "checkAuthenticationPassword", "password": password})
    elif state_type == "authorizationStateReady":
        print("[auth] ready")
        return True
    elif state_type == "authorizationStateClosed":
        print("[auth] tdlib closed; exiting")
        sys.exit(1)
    return False


# ----- Chat info -----
def handle_chat_update(update: dict) -> None:
    typ = update.get("@type")
    if typ == "updateNewChat":
        chat = update["chat"]
        chat_titles[chat["id"]] = chat.get("title", "")
    elif typ == "updateChatTitle":
        chat_titles[update["chat_id"]] = update.get("title", "")


def request_load_chats() -> None:
    td_send({
        "@type": "loadChats",
        "chat_list": {"@type": "chatListMain"},
        "limit": 500,
    })


# ----- Message handling -----
async def handle_new_message(message: dict) -> None:
    """Record + dispatch action for outgoing messages the user sent from another device."""
    if not message.get("is_outgoing"):
        return
    # Daemon-sent messages have a `sending_state` set while in flight; skip them
    # so a reply that itself matches a rule can't loop.
    if message.get("sending_state") is not None:
        return

    content = message.get("content", {})
    if content.get("@type") != "messageText":
        return
    text = content.get("text", {}).get("text", "")
    if not text:
        return

    chat_id = message["chat_id"]
    message_id = message["id"]
    if already_seen(chat_id, message_id):
        return

    record = {
        "chat_id": chat_id,
        "chat_title": chat_titles.get(chat_id, ""),
        "message_id": message_id,
        "date": message.get("date", 0),
        "text": text,
        "seen_at": time.time(),
    }
    remember(record)

    rule = match_rule(text)
    log_self_message(record, rule)

    if rule:
        reply_ctx = extract_reply_context(message)
        # Spawn so the event loop keeps draining updates while AI calls are in flight.
        asyncio.create_task(dispatch_action(text, chat_id, message_id, reply_ctx, rule))


def log_self_message(record: dict, rule: Optional[dict]) -> None:
    where = record["chat_title"] or f"chat:{record['chat_id']}"
    text = record["text"]
    if len(text) > 80:
        text = text[:77] + "..."
    if rule:
        action = rule.get("action", "reply")
        keyword = rule.get("keyword", "")
        print(f"[me] {where} | {text!r}  [{action}: {keyword!r}]")
    else:
        print(f"[me] {where} | {text!r}")


def log_action_done(action: str, result: str) -> None:
    if len(result) > 160:
        preview = result[:157] + "..."
    else:
        preview = result
    print(f"[me] -> ({action}) {preview!r}")


# ----- Event loop -----
async def event_loop() -> None:
    global authorized
    loop = asyncio.get_running_loop()

    while True:
        update = await loop.run_in_executor(_recv_executor, td_receive, 1.0)
        if update is None:
            continue

        # Route @extra-tagged responses to pending td_request callers.
        extra = update.get("@extra")
        if extra and extra in _pending_requests:
            fut = _pending_requests[extra]
            if not fut.done():
                fut.set_result(update)
            continue

        if get_config().get("debug", False):
            preview = json.dumps(update, ensure_ascii=False)
            if len(preview) > 300:
                preview = preview[:300] + "..."
            print(f"[debug] {preview}")

        upd_type = update.get("@type")
        if upd_type == "updateAuthorizationState":
            if handle_authorization_state(update["authorization_state"]):
                if not authorized:
                    authorized = True
                    request_load_chats()
        elif upd_type in ("updateNewChat", "updateChatTitle"):
            handle_chat_update(update)
        elif upd_type == "updateNewMessage" and authorized:
            await handle_new_message(update["message"])


async def main() -> None:
    # 0 = FATAL only. Synchronous so it takes effect before any other TDLib chatter.
    td_execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": 0})
    load_state()
    load_messages()
    print(f"[init] model={get_current_model() or '(unset)'}")
    if not OPENAI_API_KEY:
        print("[init] OPENAI_API_KEY not set — gpt-* models will fail")
    if not GEMINI_API_KEY:
        print("[init] GEMINI_API_KEY not set — gemini-* models disabled")
    saver_task = asyncio.create_task(periodic_save())
    try:
        await event_loop()
    finally:
        saver_task.cancel()
        if dirty:
            try:
                save_messages()
            except OSError as e:
                print(f"[save] final save failed: {e}")
        _td.td_json_client_destroy(_client)
        _recv_executor.shutdown(wait=False)
        print("[exit] cleanup complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[exit] interrupted")
