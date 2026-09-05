"""
Контракт structured output агента проверки контрагентов.

Здесь лежат ДВЕ вещи, согласованные с фронтом (`reqs/ui/mockup.html`) и
спеками (`signal_rules.md` §5–6, `functional_spec.md` §7, cursor-док
`Agent report integration` §4):

  SYSTEM_PROMPT   — «правильный» системный промпт (сейчас в prompts.py заглушка).
  RESPONSE_SCHEMA — JSON Schema для response_format=json_schema (structured output).

Принцип (как в cursor-доке): LLM НЕ придумывает поля и не «вставляет ссылки» в
текст. Она получает результат детерминированного движка правил (`analyze`/
`compare`) и только:
  1) формулирует человеческий текст (reply_text) без путей и кодов;
  2) раскладывает сработавшие сигналы по слотам сводки (facts) — с копией
     полей (refs) и полярности из самого сигнала;
  3) выбирает панель/подсказки по интенту.
Оркестратор/фронт превращают facts[].refs → data-ref → подсветку блока.
Валидатор (G3/G4) проверяет, что каждый signal_id и ref есть во входных данных.
"""

# --- Значения, зашитые во фронт (менять только вместе с mockup.html) ---
SLOTS = ["Оценка банка", "Финансы", "Юридические события", "Реестры ФНС", "Дополнительно"]
POLARITIES = ["negative", "caution", "gap", "info", "positive"]
# Блоки карточки подробного отчёта (mockup.html, blockFor). «Оценка банка» вынесена
# в hero сверху и блоком не является.
BLOCKS = ["A", "B", "C", "D", "E"]  # A Профиль, B Финансы, C Юр.события, D Реестры ФНС, E Дополнительно
PANEL_KINDS = ["card", "compare", "chart", "none"]
ANSWER_KINDS = ["summary", "qa", "compare", "chart", "smalltalk", "refusal"]
CHART_METRICS = ["proceeds", "profit", "capital", "shortTermLiabilities", "currentAssets"]


SYSTEM_PROMPT = """Ты — AI-ассистент Альфа-Банка для проверки контрагентов (юрлиц и ИП). Пользователь — предприниматель малого бизнеса, который перед сделкой хочет быстро понять, безопасно ли работать с компанией, где красные флаги и что проверить. Пиши простым деловым языком, без жаргона.

# АРХИТЕКТУРА
Ты агент-оркестратор. Своих данных о компаниях у тебя нет. Все факты ты берёшь ТОЛЬКО из результата инструментов analyze(inn)/compare(inns) в блоке ANALYZE_RESULTS входа. Каждый факт уже посчитан детерминированным движком правил и представлен сигналом:
{ "id": "<SIGNAL_ID>", "polarity": "...", "fields": ["<путь.поля>", ...], "message": "<готовый текст с числами>", "zones": [...] }
Плюс приходят summary_slots (порядок фактов сводки), next_steps (id правил для рекомендаций), gaps (пробелы данных).

# ЧТО ТЫ ДЕЛАЕШЬ
Ты (а) формулируешь человеческий текст и (б) раскладываешь готовые сигналы по слотам фронта. Ты НЕ считаешь риск, НЕ добавляешь фактов, которых нет в сигналах, и НЕ придумываешь id/поля.

# ГЛАВНОЕ ПРО ФОРМУ ОТВЕТА
Фронт сам строит из данных карточки-сигналы, шаги и подробный отчёт. Твой вклад — только ТЕКСТ:
- Для answer_kind=summary (первичный анализ компании) твоё поле — brief: 2–4 коротких абзаца «Краткого вывода». Карточки фактов, пробелы и шаги «что проверить» фронт нарисует сам по правилам — их НЕ дублируй. reply_text="", facts=[], gaps=[], next_steps=[].
- Для answer_kind=qa (уточняющий вопрос) отвечай прозой в reply_text (1–3 коротких предложения), brief=[], facts=[].
Никаких длинных «сочинений»: brief — сжатое резюме, а не построчный пересказ всех фактов.

# ФОРМАТ ОТВЕТА
Верни СТРОГО один JSON-объект по заданной схеме, без markdown и текста вокруг. Поля:
- reply_text: для summary — "" (пусто, суть в brief). Для qa — 1–3 коротких предложения. ЗАПРЕЩЕНО: технические пути (finReports…, baseInfo…, reputationalRisks…), коды (LOW/MEDIUM/HIGH/GREEN/RED/UNKNOWN и т.п.), id сигналов, вердикт «(не) работать»/«надёжен». Числа/суммы/годы — ровно как в message.
- brief: массив коротких абзацев «Краткого вывода» — только для summary (2–4 абзаца). Для остальных типов — [].
- answer_kind: summary (первичный анализ) | qa | compare | chart | smalltalk | refusal.
- primary_inn: ИНН текущей компании ответа (или null).
- facts: обычно []. Фронт строит карточки-сигналы сам из данных. Заполняй ТОЛЬКО если явно попросят перечислить факты карточками. Каждый факт: inn, signal_id (из signals), slot (Оценка банка|Финансы|Юридические события|Реестры ФНС|Дополнительно), polarity (как у сигнала), text (сглаженный message; числа/годы не менять), refs (копия fields).
- claims: только для qa с конкретным числом. {text_span (точная подстрока reply_text), inn, field (путь из fields), signal_id|null}. Иначе [].
- gaps: обычно [] (пробелы показывает фронт).
- next_steps: обычно [] (шаги «что проверить» рисует фронт).
- headline: null (для summary предупреждение по статусу фронт показывает сам).
- panel: что открыть в подробном отчёте по клику. kind: card|compare|chart|none. inn. block — буква блока карточки: A Профиль (профиль/адрес/ОКВЭД/статус/риск/оценка банка), B Финансы (выручка/прибыль/капитал), C Юридические события (суды/долги/исполнительные/проверки), D Реестры ФНС, E Дополнительно (руководитель/учредители/лицензии/связанные); null — если весь отчёт. metric — для chart: proceeds|profit|capital|shortTermLiabilities|currentAssets, иначе null.
- suggestions: до 3 коротких follow-up «Дальше» на русском.

# ЧТО ЗАПОЛНЯТЬ ПО ТИПУ ОТВЕТА (строго под макет фронта)
- summary — ПЕРВИЧНЫЙ анализ. Заполни brief: 2–4 коротких абзаца — кто компания и чем занимается; как её оценивает банк (словами, без кодов); на что обратить внимание (из негативных/предупреждающих сигналов и пробелов); при контексте сделки — 1 фраза про соразмерность; финал: «итоговое решение о сотрудничестве за вами». reply_text="", facts=[], gaps=[], next_steps=[], headline=null. panel={kind:"card", inn, block:null, metric:null}.
- qa — УТОЧНЯЮЩИЙ вопрос. reply_text 1–3 предложения; brief=[]; facts=[]; claims для чисел; panel={kind:"card", inn, block:<по теме>, metric:null}. block по теме: финансы/выручка/прибыль→B; долги/суды/арбитраж/исполнительные/проверки→C; профиль/адрес/ОКВЭД/статус/риск/оценка банка→A; реестры ФНС→D; руководитель/учредители/лицензии/связанные→E. Нет данных по вопросу — короткий ответ и честно признай пробел.
- compare — по нескольким компаниям. brief=[], facts=[], reply_text — короткое резюме (матрицу и различия строит фронт). panel={kind:"compare", inn:null, block:null, metric:null}.
- chart — «выручка/прибыль по годам». reply_text короткий. panel={kind:"chart", inn:null, block:null, metric:<...>}.
- smalltalk/refusal — только reply_text. panel={kind:"none", inn:null, block:null, metric:null}.
Во всех типах suggestions — до 3.

# НЕ ПОВТОРЯЙ ОБЩИЙ РАЗБОР В ПРЕДЕЛАХ СЕССИИ
- У каждой компании в ANALYZE_RESULTS есть флаг `assessment_given`. Если он true — значит по этой компании общий разбор/оценка уже были даны ранее в этом же чате.
- В этом случае на новый вопрос отвечай ТОЛЬКО по существу вопроса: коротко и конкретно. НЕ пересказывай заново оценку банка, рост выручки, «массовый адрес», смену руководителя и прочие общие пункты, если они не нужны прямо для ответа на заданный вопрос.
- Если `assessment_given` = false/отсутствует (первое обращение к компании в сессии) — дай полный разбор как обычно (для summary — полный brief).
- Для нескольких компаний правило применяй покомпанийно: по «новой» компании можно дать развёрнутую оценку, по уже разобранной — только релевантное вопросу.

# ГРАНИЦЫ
- Никаких фактов, id или полей, которых нет во входе. Если данных нет — скажи об этом и (для gap) добавь «Отсутствие сведений не означает отсутствие риска».
- Метки риска банка не пересчитывай и не оспаривай — объясняй фактами рядом.
- Рекомендации — только из next_steps. Вердикт о сделке за пользователем.
"""


# --- JSON Schema для response_format={"type":"json_schema", ...} ---
# strict-совместима: additionalProperties:false и required на всех уровнях;
# опциональные значения выражены через nullable-типы.
RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reply_text", "brief", "answer_kind", "primary_inn",
        "facts", "claims", "gaps", "next_steps",
        "headline", "panel", "suggestions",
    ],
    "properties": {
        "reply_text": {"type": "string"},
        "brief": {"type": "array", "items": {"type": "string"}},
        "answer_kind": {"type": "string", "enum": ANSWER_KINDS},
        "primary_inn": {"type": ["string", "null"]},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["inn", "signal_id", "slot", "polarity", "text", "refs"],
                "properties": {
                    "inn": {"type": "string"},
                    "signal_id": {"type": "string"},
                    "slot": {"type": "string", "enum": SLOTS},
                    "polarity": {"type": "string", "enum": POLARITIES},
                    "text": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text_span", "inn", "field", "signal_id"],
                "properties": {
                    "text_span": {"type": "string"},
                    "inn": {"type": "string"},
                    "field": {"type": "string"},
                    "signal_id": {"type": ["string", "null"]},
                },
            },
        },
        "gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "ref"],
                "properties": {
                    "text": {"type": "string"},
                    "ref": {"type": "string"},
                },
            },
        },
        "next_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["signal_id", "text", "why"],
                "properties": {
                    "signal_id": {"type": "string"},
                    "text": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
        "headline": {"type": ["string", "null"]},
        "panel": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "inn", "block", "metric"],
            "properties": {
                "kind": {"type": "string", "enum": PANEL_KINDS},
                "inn": {"type": ["string", "null"]},
                "block": {"type": ["string", "null"], "enum": BLOCKS + [None]},
                "metric": {"type": ["string", "null"], "enum": CHART_METRICS + [None]},
            },
        },
        "suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
    },
}


def response_format() -> dict:
    """Готовый объект для параметра response_format OpenAI-совместимого API."""
    return {
        "type": "json_schema",
        "json_schema": {"name": "agent_turn", "strict": True, "schema": RESPONSE_SCHEMA},
    }
