"""
Инициализация LLM-клиента по профилю из llm_config.json.

Профиль выбирается env-переменной ACTIVE_LLM_PROFILE. Клиент создаётся лениво
(get_llm): импорт модуля не требует ни env, ни ключа, поэтому /health отвечает
и до настройки агента, а ошибка конфигурации — понятный ValueError в момент
первого запроса, а не падение на импорте.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv, find_dotenv
from langchain_openai import ChatOpenAI

# .env из каталога сервиса (для запуска без docker; в контейнере env приходит из compose)
load_dotenv(find_dotenv())

CONFIG_PATH = Path(__file__).parent / "llm_config.json"

# Профили, которым API-ключ не нужен (локальные модели: LM Studio, vLLM) —
# в llm_config.json у них api_key_env указан как "any"
KEYLESS_API_KEY_ENV = {"", "any", "none", "null"}


@lru_cache(maxsize=1)
def load_config() -> dict:
    """Чтение JSON конфига (кэшируется: файл не меняется в рантайме)."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def active_profile() -> str:
    """Имя активного профиля с валидацией; понятная ошибка вместо падения."""
    profile = os.getenv("ACTIVE_LLM_PROFILE")
    if not profile:
        profiles = ", ".join(sorted(load_config()))
        raise ValueError(
            f"env-переменная ACTIVE_LLM_PROFILE не задана. Укажите её в .env "
            f"(доступные профили: {profiles}) или передайте явно."
        )
    if profile not in load_config():
        raise ValueError(f"Профиль '{profile}' не найден в llm_config.json")
    return profile


def _resolve_api_key(profile_name: str, profile_config: dict) -> str:
    """API-ключ профиля: из env-переменной с именем api_key_env.

    Локальным профилям (LM Studio/vLLM) ключ не нужен — отправляем placeholder.
    Пустой ключ облачного профиля — ошибка с именем переменной, а не тихая
    отправка строки-имени вместо ключа.
    """
    env_name = profile_config.get("api_key_env") or ""
    if env_name.lower() in KEYLESS_API_KEY_ENV:
        return "not-required-for-local-models"
    key = os.getenv(env_name, "").strip()
    if not key:
        raise ValueError(
            f"Не задан API-ключ профиля '{profile_name}': "
            f"переменная {env_name} пуста или не определена в окружении."
        )
    return key


def get_llm_client(profile_name: str) -> ChatOpenAI:
    """Инициализация LLM по профилю из llm_config.json."""
    profile_config = load_config()[profile_name]

    # Доп. параметры тела запроса (не входят в стандартный OpenAI-контракт).
    # Для локальных reasoning-моделей (Qwen3) через LM Studio урезаем объём
    # размышлений, иначе на слабом железе ответ занимает минуты.
    extra_body = profile_config.get("extra_body")

    client_kwargs = dict(
        model=profile_config["model"],
        base_url=profile_config["base_url"],
        api_key=_resolve_api_key(profile_name, profile_config),
        temperature=float(profile_config.get("temperature", 0.2)),
        top_p=float(profile_config.get("top_p", 0.9)),
    )
    if extra_body:
        client_kwargs["extra_body"] = extra_body

    return ChatOpenAI(**client_kwargs)


_llm: ChatOpenAI | None = None


def get_llm() -> ChatOpenAI:
    """Единственный экземпляр клиента на процесс (ленивая инициализация)."""
    global _llm
    if _llm is None:
        _llm = get_llm_client(active_profile())
    return _llm
