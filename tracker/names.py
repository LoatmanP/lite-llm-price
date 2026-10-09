"""Human-readable model names and family tags for the dashboard."""

from __future__ import annotations

import re

VENDORS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "amazon": "Amazon",
    "meta": "Meta",
    "mistral": "Mistral AI",
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "moonshotai": "Moonshot AI",
    "moonshot": "Moonshot AI",
    "writer": "Writer",
    "xai": "xAI",
    "zai": "Z.ai",
    "twelvelabs": "TwelveLabs",
    "cohere": "Cohere",
    "ai21": "AI21 Labs",
    "nvidia": "NVIDIA",
    "google": "Google",
    "minimax": "MiniMax",
}

UPPER = {"gpt", "glm", "oss", "vl", "ai"}
FAMILY_WORDS = {
    "anthropic": ("mythos", "fable", "opus", "sonnet", "haiku"),
    "openai": ("astra", "sol", "terra", "luna"),
    "amazon": ("premier", "pro", "lite", "micro", "sonic"),
}


def vendor_name(vendor: str) -> str:
    return VENDORS.get(vendor, vendor[:1].upper() + vendor[1:])


def _clean(name: str) -> str:
    name = re.sub(r":\d+$", "", name)  # :0
    name = re.sub(r"-v1$", "", name)  # -v1; later versions stay as "v2"
    name = re.sub(r"-\d{8}(?=-|$)", "", name)  # -20250514
    return name


# Vendors whose model names do not carry the brand on their own.
BRAND_PREFIX = {"deepseek": "DeepSeek"}


def display_name(model_id: str, prefix: str) -> str:
    rest = model_id[len(prefix):] if prefix and model_id.startswith(prefix) else model_id
    vendor, _, name = rest.partition(".")
    tokens: list[str] = []
    for tok in _clean(name).split("-"):
        m = re.fullmatch(r"([a-z]{3,})(\d+)", tok)  # llama3 -> llama 3
        tokens.extend([m.group(1), m.group(2)] if m else [tok])

    merged: list[str] = []
    for tok in tokens:  # 3 5 -> 3.5
        if merged and re.fullmatch(r"\d{1,2}", tok) and re.fullmatch(r"\d{1,2}(\.\d{1,2})*", merged[-1]):
            merged[-1] = f"{merged[-1]}.{tok}"
        else:
            merged.append(tok)

    words: list[str] = []
    for tok in merged:
        if tok in UPPER:
            words.append(tok.upper())
        elif re.fullmatch(r"v\d+", tok):  # version suffix, keep lowercase
            words.append(tok)
        elif re.fullmatch(r"\d+b", tok):
            words.append(tok.upper())
        elif re.fullmatch(r"[a-z]\d[\d.]*", tok):  # r1, x4, k3, v3.2
            words.append(tok.upper())
        elif tok[:1].isalpha():
            words.append(tok[:1].upper() + tok[1:])
        else:
            words.append(tok)

    text = " ".join(words)
    text = re.sub(r"\b(GPT|GLM) (?=\d)", r"\1-", text)
    if vendor in BRAND_PREFIX:
        text = f"{BRAND_PREFIX[vendor]} {text}"
    return text


def family(model_id: str, vendor: str) -> str | None:
    words = FAMILY_WORDS.get(vendor)
    if not words:
        return None
    tokens = set(re.split(r"[-.:]", model_id.lower()))
    for word in words:
        if word in tokens:
            return word
    return None
