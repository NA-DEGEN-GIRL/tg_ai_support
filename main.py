"""
tg_self_reply: Telegram self-reply daemon with local CLI-backed LLM support.

Listens for outgoing messages from your own Telegram user account and dispatches
configured actions when text matches a rule:

  - reply        : send a static text reply
  - translate    : translate through Codex CLI headless mode
  - ask_llm      : ask a logged-in Codex / Claude / Grok interactive CLI session
  - set_provider : switch the default interactive provider

The daemon uses TDLib through ctypes. AI API keys are not required; LLM work is
delegated to already-authenticated local CLI tools.
"""

import asyncio
import copy
import ctypes
import html as html_module
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
MESSAGES_FILE = ROOT / "messages.json"
STATE_FILE = ROOT / "state.json"

DEFAULT_LLM_CONFIG = {
    "default_provider": "codex",
    "continue_keyword": "cont",
    "bridge_dir": ".llm_bridge",
    "poll_interval_seconds": 1.0,
    "output_stable_seconds": 1.0,
    "translation": {
        "provider": "codex",
        "timeout_seconds": 120,
        "command": (
            "codex exec -C {root} --sandbox read-only "
            "--ask-for-approval never -o {output} -"
        ),
    },
    "providers": {
        "codex": {
            "session_name": "tgself-codex",
            "command": (
                "codex --no-alt-screen --search -C {root} "
                "--dangerously-bypass-approvals-and-sandbox"
            ),
            "continue_command": (
                "codex resume --last --no-alt-screen --search -C {root} "
                "--dangerously-bypass-approvals-and-sandbox"
            ),
            "prompt_delivery": "argv",
            "timeout_seconds": 900,
            "startup_delay_seconds": 3.0,
            "reset_strategy": "recreate",
        },
        "claude": {
            "session_name": "tgself-claude",
            "command": "claude --dangerously-skip-permissions",
            "continue_command": "claude --continue --dangerously-skip-permissions",
            "prompt_delivery": "argv",
            "timeout_seconds": 900,
            "startup_delay_seconds": 3.0,
            "reset_strategy": "recreate",
        },
        "grok": {
            "session_name": "tgself-grok",
            "command": (
                "grok --no-alt-screen --cwd {root} --always-approve "
                "--permission-mode bypassPermissions"
            ),
            "prompt_delivery": "paste",
            "submit_keys": ["Enter"],
            "timeout_seconds": 900,
            "startup_delay_seconds": 3.0,
            "reset_strategy": "recreate",
        },
    },
}


# ----- Config (hot reload on mtime) -----
_config_cache: dict = {}
_config_mtime: float = 0.0


def get_config() -> dict:
    """Reparse config.json when its mtime changes; return cached config otherwise."""
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


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def get_llm_config() -> dict:
    return _deep_merge(DEFAULT_LLM_CONFIG, get_config().get("llm", {}))


def get_bridge_root() -> Path:
    bridge_dir = get_llm_config().get("bridge_dir", ".llm_bridge")
    path = Path(bridge_dir)
    if not path.is_absolute():
        path = ROOT / path
    return path


def get_rules() -> list:
    return get_config().get("rules", [])


def get_continue_keyword() -> str:
    return str(get_llm_config().get("continue_keyword", "cont")).strip()


def get_provider_names() -> set[str]:
    return set(get_llm_config().get("providers", {}).keys())


def get_provider_config(provider: str) -> Optional[dict]:
    return get_llm_config().get("providers", {}).get(provider)


def get_default_provider() -> str:
    cfg = get_llm_config()
    default = cfg.get("default_provider", "codex")
    if default in cfg.get("providers", {}):
        return default
    providers = list(cfg.get("providers", {}).keys())
    return providers[0] if providers else ""


def is_valid_provider(provider: str) -> bool:
    return provider in get_provider_names()


def format_template(template: str, **values) -> str:
    quoted = {key: shlex.quote(str(value)) for key, value in values.items()}
    return template.format(**quoted)


# Bootstrap fields that do not hot-reload.
get_config()
if not _config_cache:
    raise RuntimeError("config.json is required; copy config.json.example first")
API_ID = int(_config_cache["api_id"])
API_HASH = _config_cache["api_hash"]
TDJSON_PATH = os.path.expanduser(_config_cache["tdjson_path"])
MAX_RECENT = int(_config_cache.get("max_recent_messages", 500))
SAVE_INTERVAL = float(_config_cache.get("save_interval_seconds", 60))
DB_DIR = str(ROOT / "tdlib")


# ----- Runtime state -----
recent_messages: "deque[dict]" = deque(maxlen=MAX_RECENT)
chat_titles: dict = {}
authorized = False
dirty = False
_state: dict = {}
_provider_locks: dict[str, asyncio.Lock] = {}


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


def get_current_provider() -> str:
    provider = _state.get("current_provider") or get_default_provider()
    if is_valid_provider(provider):
        return provider
    return get_default_provider()


def set_current_provider(provider: str) -> None:
    _state["current_provider"] = provider
    try:
        save_state()
    except OSError as e:
        print(f"[state] save failed: {e}")


def get_provider_lock(provider: str) -> asyncio.Lock:
    lock = _provider_locks.get(provider)
    if lock is None:
        lock = asyncio.Lock()
        _provider_locks[provider] = lock
    return lock


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
    """Synchronous TDLib call. Used for parseTextEntities."""
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
def _strip_continue_marker(text: str) -> tuple[str, bool]:
    keyword = get_continue_keyword()
    if not keyword:
        return text, False
    stripped = text.rstrip()
    lower = stripped.lower()
    marker = f" {keyword.lower()}"
    if lower.endswith(marker):
        return stripped[: -len(marker)].rstrip(), True
    return text, False


def _rule_matches_text(text: str, rule: dict) -> bool:
    stripped = text.strip()
    lower = stripped.lower()
    keyword = rule.get("keyword", "")
    if not keyword:
        return False
    match_type = rule.get("match", "exact")
    kw_lower = keyword.lower()
    if match_type == "exact":
        return stripped == keyword
    if match_type == "contains":
        return keyword in text
    if match_type == "prefix":
        return lower.startswith(kw_lower)
    if match_type == "suffix":
        return lower.endswith(kw_lower)
    return False


def match_rule(text: str) -> Optional[dict]:
    base_text, continue_context = _strip_continue_marker(text)
    for rule in get_rules():
        if rule.get("action") != "set_provider":
            continue
        if _rule_matches_text(text, rule):
            return copy.deepcopy(rule)

    for rule in get_rules():
        candidate = base_text if rule.get("action") == "ask_llm" else text
        if not _rule_matches_text(candidate, rule):
            continue
        matched = copy.deepcopy(rule)
        if matched.get("action") == "ask_llm":
            matched["_continue"] = continue_context
            matched["_effective_text"] = base_text
        return matched
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


# ----- Markdown -> Telegram HTML converter -----
_PLACEHOLDER_RE = re.compile(r"\x00C(\d+)\x00")


def md_to_telegram_html(text: str) -> str:
    """Convert common standard markdown to Telegram-flavored HTML."""
    segments: list = []

    def stash(m):
        segments.append(m.group(0))
        return f"\x00C{len(segments) - 1}\x00"

    text = re.sub(r"```(?:[\w+-]*\n)?.*?```", stash, text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", stash, text)

    def stash_link(m):
        link_text = m.group(1)
        url = m.group(2)
        text_esc = html_module.escape(link_text, quote=False)
        url_esc = html_module.escape(url, quote=True)
        anchor = f'<a href="{url_esc}">{text_esc}</a>'
        segments.append(anchor)
        return f"\x00C{len(segments) - 1}\x00"

    text = re.sub(r"\[([^\[\]]+?)\]\(([^()\s]+?)\)", stash_link, text)
    text = html_module.escape(text, quote=False)

    text = re.sub(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"(?<![A-Za-z0-9_*])\*\*(.+?)\*\*(?![A-Za-z0-9_*])", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![A-Za-z0-9_])__(.+?)__(?![A-Za-z0-9_])", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![A-Za-z0-9_*])\*(?!\*)(.+?)(?<!\*)\*(?![A-Za-z0-9_*])", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![A-Za-z0-9_])_(?!_)(.+?)(?<!_)_(?![A-Za-z0-9_])", r"<i>\1</i>", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text, flags=re.DOTALL)
    text = re.sub(r"^[ \t]*[-*][ \t]+", "- ", text, flags=re.MULTILINE)

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
        inner = raw[1:-1]
        return f"<code>{html_module.escape(inner, quote=False)}</code>"

    return _PLACEHOLDER_RE.sub(restore, text)


def format_text(text: str) -> dict:
    """Convert markdown text to TDLib formattedText, falling back to plain text."""
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
    """Fetch a message via TDLib and extract text or caption."""
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
    """If `message` is itself a reply, return the original chat/message ids."""
    reply_to = message.get("reply_to")
    if not reply_to or reply_to.get("@type") != "messageReplyToMessage":
        return None
    return {
        "chat_id": reply_to.get("chat_id") or message.get("chat_id"),
        "message_id": reply_to.get("message_id"),
    }


# ----- CLI bridge helpers -----
def make_request_paths(provider: str, action: str) -> dict:
    request_id = f"{int(time.time())}-{provider}-{uuid.uuid4().hex[:8]}"
    request_dir = get_bridge_root() / "requests" / request_id
    request_dir.mkdir(parents=True, exist_ok=False)
    return {
        "id": request_id,
        "dir": request_dir,
        "input": request_dir / "input.md",
        "output": request_dir / "output.md",
        "meta": request_dir / "meta.json",
        "action": action,
        "provider": provider,
    }


def write_request_files(paths: dict, prompt: str, meta: dict) -> None:
    paths["input"].write_text(prompt, encoding="utf-8")
    payload = {
        "request_id": paths["id"],
        "provider": paths["provider"],
        "action": paths["action"],
        "created_at": time.time(),
        "input_path": str(paths["input"]),
        "output_path": str(paths["output"]),
    }
    payload.update(meta)
    paths["meta"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def wait_for_output(path: Path, timeout: float) -> Optional[str]:
    cfg = get_llm_config()
    poll = float(cfg.get("poll_interval_seconds", 1.0))
    stable_seconds = float(cfg.get("output_stable_seconds", 1.0))
    deadline = time.monotonic() + timeout
    last_size = -1
    stable_since: Optional[float] = None

    while time.monotonic() < deadline:
        if path.exists():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if size > 0:
                now = time.monotonic()
                if size == last_size:
                    if stable_since is None:
                        stable_since = now
                    if now - stable_since >= stable_seconds:
                        text = path.read_text(encoding="utf-8").strip()
                        if text:
                            return text
                else:
                    last_size = size
                    stable_since = now
        await asyncio.sleep(poll)
    return None


def run_checked(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs)


def tmux_session_exists(session_name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def kill_tmux_session(session_name: str) -> None:
    subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_tmux_session(provider: str, provider_cfg: dict,
                       command_template: Optional[str] = None,
                       initial_prompt: Optional[str] = None) -> None:
    session_name = provider_cfg["session_name"]
    command = format_template(command_template or provider_cfg["command"], root=ROOT)
    if initial_prompt:
        command = f"{command} {shlex.quote(initial_prompt)}"
    run_checked([
        "tmux",
        "new-session",
        "-d",
        "-s",
        session_name,
        "-c",
        str(ROOT),
        command,
    ])
    print(f"[llm] started {provider} tmux session: {session_name}")


async def ensure_provider_session(provider: str, continue_context: bool,
                                  command_template: Optional[str] = None,
                                  initial_prompt: Optional[str] = None) -> tuple[str, dict]:
    provider_cfg = get_provider_config(provider)
    if not provider_cfg:
        raise RuntimeError(f"unknown provider: {provider}")

    session_name = provider_cfg["session_name"]
    reset_strategy = provider_cfg.get("reset_strategy", "recreate")
    should_reset = (not continue_context and reset_strategy == "recreate") or bool(initial_prompt)

    if should_reset and tmux_session_exists(session_name):
        kill_tmux_session(session_name)

    started = False
    if not tmux_session_exists(session_name):
        start_tmux_session(
            provider,
            provider_cfg,
            command_template=command_template,
            initial_prompt=initial_prompt,
        )
        started = True

    if started or should_reset:
        await asyncio.sleep(float(provider_cfg.get("startup_delay_seconds", 3.0)))

    return session_name, provider_cfg


def paste_to_tmux(session_name: str, text: str) -> None:
    target = f"{session_name}:0.0"
    buffer_name = f"tgself-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["tmux", "load-buffer", "-b", buffer_name, "-"],
        input=text,
        text=True,
        check=True,
        capture_output=True,
    )
    try:
        run_checked(["tmux", "paste-buffer", "-b", buffer_name, "-t", target])
    finally:
        subprocess.run(
            ["tmux", "delete-buffer", "-b", buffer_name],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def submit_tmux_prompt(session_name: str, submit_keys: list[str]) -> None:
    target = f"{session_name}:0.0"
    for key in submit_keys or ["Enter"]:
        run_checked(["tmux", "send-keys", "-t", target, key])


def build_translation_prompt(text: str, lang: str) -> str:
    return (
        f"Translate the following text to {lang}.\n"
        "Output only the translation. Do not include quotes, commentary, "
        "explanations, markdown fences, or extra labels.\n\n"
        "Text:\n"
        f"{text}"
    )


def build_interactive_input(provider: str, prompt: str, output_path: Path,
                            continue_context: bool) -> str:
    return f"{prompt.strip()}\n"


def build_tmux_prompt(input_path: Path, output_path: Path) -> str:
    return (
        f"Read {input_path}. "
        f"Write only your final answer to {output_path}."
    )


async def call_translation_cli(source: str, lang: str) -> str:
    cfg = get_llm_config().get("translation", {})
    provider = cfg.get("provider", "codex")
    timeout = float(cfg.get("timeout_seconds", 120))
    paths = make_request_paths(provider, "translate")
    prompt = build_translation_prompt(source, lang)
    write_request_files(paths, prompt, {
        "target_lang": lang,
        "mode": "headless",
    })

    command_template = cfg.get("command") or DEFAULT_LLM_CONFIG["translation"]["command"]
    command = format_template(command_template, root=ROOT, output=paths["output"])
    args = shlex.split(command)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ROOT),
        )
    except FileNotFoundError as e:
        print(f"[llm] translation command not found: {e}")
        return "[error] translation command not found"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        print(f"[llm] {provider} translation timed out after {int(timeout)}s")
        return "[error] translation timed out"

    if proc.returncode != 0:
        err = (stderr or stdout).decode("utf-8", errors="replace").strip()
        print(f"[llm] {provider} translation failed ({proc.returncode}): {err[:500]}")
        return "[error] translation failed"

    if paths["output"].exists():
        result = paths["output"].read_text(encoding="utf-8").strip()
        if result:
            return result

    result = stdout.decode("utf-8", errors="replace").strip()
    return result or "[error] empty translation output"


async def call_interactive_cli(provider: str, prompt: str,
                               continue_context: bool = False) -> str:
    if not is_valid_provider(provider):
        return f"[error] unknown provider: {provider}"

    lock = get_provider_lock(provider)
    async with lock:
        paths = make_request_paths(provider, "ask_llm")
        interactive_input = build_interactive_input(
            provider,
            prompt,
            paths["output"],
            continue_context,
        )
        write_request_files(paths, interactive_input, {
            "mode": "interactive",
            "continue_context": continue_context,
        })

        try:
            tmux_prompt = build_tmux_prompt(paths["input"], paths["output"])
            provider_cfg = get_provider_config(provider) or {}
            prompt_delivery = provider_cfg.get("prompt_delivery", "paste")
            command_template = None
            initial_prompt = None
            if prompt_delivery == "argv":
                if continue_context and provider_cfg.get("continue_command"):
                    command_template = provider_cfg["continue_command"]
                    initial_prompt = tmux_prompt
                elif not continue_context:
                    command_template = provider_cfg.get("command")
                    initial_prompt = tmux_prompt
            session_name, provider_cfg = await ensure_provider_session(
                provider,
                continue_context,
                command_template=command_template,
                initial_prompt=initial_prompt,
            )
            if initial_prompt is None:
                paste_to_tmux(session_name, tmux_prompt)
                submit_tmux_prompt(session_name, provider_cfg.get("submit_keys", ["Enter"]))
        except (subprocess.CalledProcessError, RuntimeError) as e:
            print(f"[llm] {provider} bridge failed: {e}")
            return "[error] LLM bridge failed"

        timeout = float(provider_cfg.get("timeout_seconds", 900))
        result = await wait_for_output(paths["output"], timeout)
        if result:
            return result
        print(f"[llm] {provider} timed out after {int(timeout)}s; session={session_name}")
        return "[error] LLM response timed out"


# ----- Action implementations -----
async def _do_translate(text: str, reply_ctx: Optional[dict], rule: dict) -> str:
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

    return await call_translation_cli(source, target_lang)


async def _do_ask_llm(text: str, reply_ctx: Optional[dict], rule: dict) -> str:
    keyword = rule.get("keyword", "")
    provider = rule.get("provider") or get_current_provider()
    effective_text = rule.get("_effective_text") or text
    continue_context = bool(rule.get("_continue"))
    user_part = strip_suffix(effective_text, keyword)

    if reply_ctx:
        source = await get_message_text(reply_ctx["chat_id"], reply_ctx["message_id"])
        if not source:
            return "[error] couldn't fetch original message"
        prompt = f"{user_part}\n\n---\n{source}" if user_part else source
    else:
        if not user_part:
            return "[error] empty prompt"
        prompt = user_part

    return await call_interactive_cli(provider, prompt, continue_context=continue_context)


def _do_set_provider(text: str, rule: dict) -> str:
    keyword = rule.get("keyword", "")
    new_provider = strip_prefix(text, keyword).strip().lower()
    if not new_provider:
        return "[error] no provider name"
    if not is_valid_provider(new_provider):
        providers = ", ".join(sorted(get_provider_names()))
        return f"[error] unknown provider: {new_provider}. Available: {providers}"
    set_current_provider(new_provider)
    return f"[ok] provider -> {new_provider}"


def _provider_for_rule(rule: dict) -> str:
    return rule.get("provider") or get_current_provider()


async def dispatch_action(text: str, chat_id: int, message_id: int,
                           reply_ctx: Optional[dict], rule: dict) -> None:
    action = rule.get("action", "reply")
    try:
        if action == "reply":
            result = rule.get("text", "")
        elif action == "translate":
            result = await _do_translate(text, reply_ctx, rule)
        elif action == "ask_llm":
            result = await _do_ask_llm(text, reply_ctx, rule)
        elif action == "set_provider":
            result = _do_set_provider(text, rule)
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
    """Record + dispatch action for outgoing messages from another device."""
    if not message.get("is_outgoing"):
        return
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
        asyncio.create_task(dispatch_action(text, chat_id, message_id, reply_ctx, rule))


def log_self_message(record: dict, rule: Optional[dict]) -> None:
    where = record["chat_title"] or f"chat:{record['chat_id']}"
    text = record["text"]
    if len(text) > 80:
        text = text[:77] + "..."
    if rule:
        action = rule.get("action", "reply")
        keyword = rule.get("keyword", "")
        marker = " cont" if rule.get("_continue") else ""
        print(f"[me] {where} | {text!r}  [{action}: {keyword!r}{marker}]")
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
    td_execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": 0})
    load_state()
    load_messages()
    bridge_root = get_bridge_root()
    bridge_root.mkdir(parents=True, exist_ok=True)
    print(f"[init] provider={get_current_provider() or '(unset)'} bridge={bridge_root}")
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
