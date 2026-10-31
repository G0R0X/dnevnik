"""Мелкие помощники для работы с ответами ИИ."""

import json
import re


def extract_json(text: str) -> dict:
    """Достаёт JSON из ответа модели, устойчиво к ```json ... ``` обёрткам."""
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # последняя попытка — взять самое большое {...} в тексте
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"JSON not found in AI response: {text[:200]}")
