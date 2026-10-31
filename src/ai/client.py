"""Клиент ИИ: RouterAI (по умолчанию) или GigaChat.

Провайдер и модель выбираются переменными окружения:
    AI_PROVIDER=routerai|gigachat   — по умолчанию routerai
    AI_MODEL=<id модели>            — по умолчанию z-ai/glm-5.3-flash
    AI_REASONING_EFFORT=low|medium|high|none — по умолчанию medium
    AI_TEMPERATURE=<число>          — по умолчанию 0 (стабильный разбор ДЗ)

RouterAI — OpenAI-совместимый API, доступен из России. «Усилие размышлений»
сильно влияет на цену: reasoning-токены составляют основную часть ответа.
"""

import base64
import json
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from gigachat import GigaChat

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CA_BUNDLE = PROJECT_ROOT / "russian_trusted_root_ca_pem.crt"

ROUTERAI_BASE_URL = os.getenv("ROUTERAI_BASE_URL", "https://routerai.ru/api/v1")
DEFAULT_ROUTERAI_MODEL = "z-ai/glm-5.3-flash"
DEFAULT_GIGACHAT_MODEL = "GigaChat-3-Ultra"


def _as_messages(messages) -> list[dict]:
    """Приводит вход к списку сообщений (строку оборачивает в user-сообщение)."""
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    return list(messages)


class AIClient:
    """Общий интерфейс: текст или текст с картинками → строка ответа."""

    async def generate(self, messages) -> str:
        raise NotImplementedError

    async def generate_with_images(self, messages, images: dict[str, bytes]) -> str:
        raise NotImplementedError


class RouterAIClient(AIClient):
    """Клиент RouterAI: один POST на вызов, картинки — data URL в content."""

    def __init__(self, api_key: str | None, model: str = DEFAULT_ROUTERAI_MODEL,
                 base_url: str = ROUTERAI_BASE_URL, timeout: float = 600,
                 reasoning_effort: str = "medium", temperature: float = 0.0):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self.temperature = temperature
        self.last_usage: dict = {}

    async def _post(self, payload: dict) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "ROUTERAI_API_KEY не задан — добавь его в .env")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{self.base_url}/chat/completions",
                                    headers=headers, json=payload) as response:
                body = await response.text()
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}: {body[:500]}")
                return json.loads(body)

    def _payload(self, messages) -> dict:
        payload = {
            "model": self.model,
            "messages": _as_messages(messages),
            "temperature": self.temperature,
        }
        if self.reasoning_effort and self.reasoning_effort != "none":
            payload["reasoning"] = {"effort": self.reasoning_effort}
        return payload

    async def generate(self, messages) -> str:
        data = await self._post(self._payload(messages))
        self.last_usage = data.get("usage") or {}
        return data["choices"][0]["message"]["content"]

    async def generate_with_images(self, messages, images: dict[str, bytes]) -> str:
        """Текст первым, затем картинки data URL (так рекомендует RouterAI)."""
        messages = _as_messages(messages)
        last = dict(messages[-1])
        if isinstance(last.get("content"), str):
            parts = [{"type": "text", "text": last["content"]}]
        else:
            parts = list(last["content"])
        for name, data in images.items():
            suffix = Path(name).suffix.lower().lstrip(".") or "png"
            if suffix == "jpg":
                suffix = "jpeg"
            encoded = base64.b64encode(data).decode()
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/{suffix};base64,{encoded}"},
            })
        last["content"] = parts
        messages[-1] = last
        return await self.generate(messages)


class GigaChatClient(AIClient):
    """Клиент GigaChat. Соединение и токен держим между вызовами.

    Скан учебника — это тысячи вызовов подряд; если создавать GigaChat
    на каждый запрос, он каждый раз заново проходит OAuth.
    """

    def __init__(self, credentials, scope, model, timeout: float = 180):
        self.credentials = credentials
        self.scope = scope
        self.model = model
        self.timeout = timeout
        self._client: GigaChat | None = None

    def _get_client(self) -> GigaChat:
        if self._client is None:
            self._client = GigaChat(
                credentials=self.credentials,
                scope=self.scope,
                model=self.model,
                timeout=self.timeout,
                ca_bundle_file=str(CA_BUNDLE),
            )
        return self._client

    async def generate(self, messages) -> str:
        response = await self._get_client().achat.create({
            "messages": _as_messages(messages),
            "model": self.model,
        })
        return response.messages[0].content[0].text

    async def generate_with_images(self, messages, images: dict[str, bytes]) -> str:
        """Изображения загружаются в GigaChat через files API."""
        client = self._get_client()
        file_ids = []
        for name, data in images.items():
            uploaded = await client.aupload_file(file=(name, data))
            file_ids.append(uploaded.id_)

        messages = _as_messages(messages)
        last = dict(messages[-1])
        if isinstance(last.get("content"), str):
            parts = [{"text": last["content"]}]
        else:
            parts = list(last["content"])
        for file_id in file_ids:
            parts.append({"files": [{"id": file_id}]})
        last["content"] = parts
        messages[-1] = last

        response = await client.achat.create({
            "messages": messages,
            "model": self.model,
        })
        return response.messages[0].content[0].text


def build_client(provider: str, model: str | None = None) -> AIClient:
    """Собирает клиент по имени провайдера. `model` переопределяет AI_MODEL."""
    chosen = model or os.getenv("AI_MODEL")
    if provider == "gigachat":
        return GigaChatClient(
            credentials=os.getenv("GIGACHAT_API_KEY"),
            scope="GIGACHAT_API_PERS",
            model=chosen or DEFAULT_GIGACHAT_MODEL,
        )
    return RouterAIClient(
        api_key=os.getenv("ROUTERAI_API_KEY"),
        model=chosen or DEFAULT_ROUTERAI_MODEL,
        reasoning_effort=os.getenv("AI_REASONING_EFFORT") or "medium",
        temperature=float(os.getenv("AI_TEMPERATURE") or 0),
    )


provider = os.getenv("AI_PROVIDER", "routerai").strip().lower()
aiclient = build_client(provider)

# Поиск номеров на страницах — много дешёвых вызовов по одной странице,
# поэтому по умолчанию бесплатный GigaChat.
search_provider = os.getenv("AI_SEARCH_PROVIDER", "gigachat").strip().lower()
search_client = aiclient if search_provider == provider else build_client(search_provider)


async def main():
    print(await aiclient.generate("Реши уравнение 2x + 5 = 17. Объясни решение по шагам."))
    usage = getattr(aiclient, "last_usage", None)
    if usage:
        print(f"\n[usage] промпт: {usage.get('prompt_tokens')}, "
              f"ответ: {usage.get('completion_tokens')}, "
              f"цена: {usage.get('cost')} ₽")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
