"""Clean stored messages and build safe FTS5 token streams and queries."""

import re
import unicodedata


CJK_RANGES = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
CJK_CHARACTER = re.compile("[" + CJK_RANGES + "]")
_TOKENS = re.compile(
    "[" + CJK_RANGES + "]+|(?:(?![" + CJK_RANGES + "])\\w)+"
)
_INJECTED = re.compile(
    r"<(system-reminder|agentmem-context|local-command-caveat|command-message)>"
    r"[\s\S]*?</\1>"
)
_REDACTIONS = (
    (
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
        r"-----END [A-Z ]*PRIVATE KEY-----",
    ),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("openai_key", r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
    ("aws_key", r"AKIA[0-9A-Z]{16}"),
    ("slack_token", r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("google_key", r"AIza[0-9A-Za-z_\-]{35}"),
    ("hf_token", r"hf_[A-Za-z0-9]{30,}"),
    ("jwt", r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("bearer", r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]{20,}"),
)
_REDACTION_PATTERNS = tuple((kind, re.compile(pattern)) for kind, pattern in _REDACTIONS)
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
    r"\b(\s*[:=]\s*)[\"']?[^\s\"']{6,}[\"']?"
)


def clean_text(text: str) -> str:
    """Remove injected context, redact secrets, and cap the stored message."""
    text = _INJECTED.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for kind, pattern in _REDACTION_PATTERNS:
        text = pattern.sub("[REDACTED:" + kind + "]", text)
    text = _ASSIGNMENT.sub(
        lambda match: match.group(1) + match.group(2) + "[REDACTED:assignment]",
        text,
    )
    if len(text) > 20000:
        text = text[:20000] + "…[truncated]"
    return text


def index_tokens(text: str) -> str:
    """NFKC-normalize words and split each CJK run into adjacent bigrams."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens = []
    for match in _TOKENS.finditer(normalized):
        word = match.group()
        if CJK_CHARACTER.fullmatch(word[0]) and len(word) > 1:
            tokens.extend(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.append(word)
    return " ".join(tokens)


def query_terms(query: str) -> list[str]:
    """Return whitespace-separated user terms that contain searchable tokens."""
    return [term for term in query.split() if index_tokens(term)]


def build_query(query: str, operator: str = "AND") -> str:
    """Build quoted phrases so user punctuation never becomes FTS syntax."""
    if operator not in {"AND", "OR"}:
        raise ValueError("查询运算符必须为 AND 或 OR。")
    phrases = ['"' + index_tokens(term) + '"' for term in query_terms(query)]
    return (" " + operator + " ").join(phrases)
