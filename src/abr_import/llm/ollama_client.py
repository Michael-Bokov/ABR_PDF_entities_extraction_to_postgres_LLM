from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


def check_ollama(base_url: str, model: str, timeout: int = 10) -> None:
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise OllamaError(
            f"Ollama недоступна: {base_url}. Запустите: docker compose up -d ollama"
        ) from exc

    names = {m.get("name", "") for m in data.get("models", [])}
    # ollama может вернуть "llama3.1:8b" или с суффиксом
    if not any(model in n or n.startswith(model.split(":")[0]) for n in names):
        raise OllamaError(
            f"Модель '{model}' не найдена в Ollama. "
            f"Выполните: docker compose exec ollama ollama pull {model}"
        )


def generate_json(
    *,
    base_url: str,
    model: str,
    prompt: str,
    system: str,
    timeout: int = 600,
    temperature: float = 0.0,
) -> str:
    url = f"{base_url.rstrip('/')}/api/generate"
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "format": "json",
            "options": {"temperature": temperature},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise OllamaError(f"Ошибка запроса Ollama: {exc}") from exc

    text = data.get("response", "")
    if not text:
        raise OllamaError("Пустой ответ Ollama")
    return text


def parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        return json.loads(match.group())
    raise OllamaError(f"Не удалось разобрать JSON: {raw[:300]}...")
