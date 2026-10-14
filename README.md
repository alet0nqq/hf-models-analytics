# hf-models-analytics

Учебный ETL-проект: сбор данных о моделях **Hugging Face Hub** через
официальный REST API, хранение в **SQLite** с инкрементальной подгрузкой и
**5 разноплановых аналитических запросов** на `pandas` + `SQLAlchemy`.

Лабораторная работа №1 (подготовительная): цель — получить данные, уложить их
в реляционную базу с возможностью обновления и построить первичную аналитику
экономики открытых ИИ-моделей.

---

## Почему этот источник

Рынок open-source ИИ сместился от «числа параметров» к **лицензиям, эффективности
инференса и экосистемам вендоров**. Данные Hugging Face Hub удобно получать через
бесплатный официальный API `https://huggingface.co/api/models` — чистый JSON с
пагинацией и фильтрацией, без парсинга HTML и риска блокировок.

---

## Ключевые особенности архитектуры

- **Decoupled ETL:** сбор (`collector`) отделён от записи в БД (`loader`),
  оркестрирует всё `main.py`.
- **Устойчивость к сбоям:** HTTP-сессия с ретраями и экспоненциальным бэкоффом;
  страницы сохраняются в БД **по мере скачивания**, поэтому обрыв соединения не
  теряет уже собранные данные.
- **Нормализованная схема (1:M):** авторы и лицензии вынесены в справочники,
  модели связаны с ними внешними ключами.
- **Идемпотентный Upsert:** `INSERT ... ON CONFLICT(model_id) DO UPDATE` —
  повторные запуски не плодят дубли, а лишь освежают `downloads`/`likes`.
- **Инкрементальная подгрузка:** режим `incremental` запрашивает только модели,
  созданные позже последней записи в БД (`createdAt > MAX(created_at)`).
- **Get-or-Create с кэшем:** справочники наполняются через кэш в памяти —
  минимум транзакций к диску.
- **Пакетная обработка:** коммиты выполняются пачками (`batch_size`).
- **Категоризация лицензий:** каждая лицензия один раз классифицируется
  (`open` / `copyleft` / `restricted` / `noncommercial` / `other`), что
  упрощает аналитические запросы.

---

## Схема базы данных

База `hf_models.db` создаётся в режиме WAL (`PRAGMA journal_mode = WAL`) с
включёнными внешними ключами (`PRAGMA foreign_keys = ON`).

```text
┌──────────────────────┐        ┌───────────────────────────────┐        ┌───────────────────────────┐
│       authors        │        │            models             │        │         licenses          │
├──────────────────────┤        ├───────────────────────────────┤        ├───────────────────────────┤
│ id (PK, AutoInc)     │ 1    M │ id (PK, AutoInc)              │ M    1 │ id (PK, AutoInc)          │
│ name (UNIQUE, INDEX) │◄───────┤ author_id  (FK -> authors.id) │        │ name (UNIQUE, INDEX)      │
└──────────────────────┘        │ license_id (FK -> licenses.id)├───────►│ category (INDEX)          │
                                │ model_id (UNIQUE, INDEX)      │        └───────────────────────────┘
                                │ pipeline_tag (INDEX)          │
                                │ downloads (INDEX), likes      │
                                │ created_at (DATETIME, INDEX)  │
                                │ tags_json (TEXT)              │
                                │ updated_at (DATETIME)         │
                                └───────────────────────────────┘
```

Поле `models.model_id` (например, `meta-llama/Llama-3.1-8B`) — естественный
уникальный ключ, по которому работает upsert.

---

## Структура проекта

```
hf-models-analytics/
│
├── models.py             # SQLAlchemy 2.0: Author, License, Model + engine + init_db
├── collector.py          # Сбор с HF API (пагинация, ретраи, parse_model)
├── loader.py             # Upsert в БД, get-or-create справочников, классификация лицензий
├── main.py               # CLI-оркестратор (init / collect / refresh / incremental / stats / all)
│
├── notebooks/
│   └── analytics.ipynb   # 5 аналитических запросов (pandas + SQLAlchemy) с графиками и выводами
│
├── pyproject.toml        # Зависимости и метаданные (uv)
├── uv.lock               # Зафиксированные версии зависимостей
├── .python-version       # Закреплённая версия Python
├── hf_models.db          # SQLite-база (в .gitignore — не коммитится)
└── README.md
```

---

## Установка и настройка

Проект использует менеджер виртуальных окружений **[uv](https://docs.astral.sh/uv/)**.

```bash
# установить uv (если ещё не установлен)
curl -LsSf https://astral.sh/uv/install.sh | sh

# перейти в каталог проекта и развернуть окружение из uv.lock
cd hf-models-analytics
uv sync
```

Все команды ниже запускаются через `uv run` (окружение активируется автоматически).

---

## Запуск пайплайна

```bash
# 0) инициализировать БД (создать таблицы, включить WAL и FK)
uv run python main.py init

# 1) первичный сбор топ-4000 моделей по downloads
uv run python main.py collect --limit 4000

# 2) освежить метрики (downloads/likes) топ-500 моделей — идемпотентный upsert
uv run python main.py refresh --limit 500

# 3) инкрементальная подгрузка только новых моделей (createdAt > последней в БД)
uv run python main.py incremental --limit 1000

# полный первичный прогон (init + collect)
uv run python main.py all --limit 4000

# показать количество строк в таблицах
uv run python main.py stats
```

Каждый модуль можно запустить и автономно:

```bash
uv run python models.py     # создать hf_models.db
uv run python loader.py     # самопроверка классификации лицензий + статистика БД
uv run python collector.py  # smoke-тест сбора с HF API
```

---

## Аналитика: 5 запросов (`notebooks/analytics.ipynb`)

Все запросы строятся выражениями SQLAlchemy (`select`, `func`) и/или читаются в
`pandas` через `pd.read_sql`, затем доагрегируются и визуализируются
(`matplotlib` / `seaborn`).

1. **Концентрация рынка вендоров** — суммарные загрузки по авторам, доля топ-10
   (**CR10**) и индекс Херфиндаля–Хиршмана (**HHI**).
2. **Лицензионный ландшафт** — распределение моделей и загрузок по категориям
   лицензий (`open` / `copyleft` / `restricted` / `noncommercial` / `other` /
   `unknown`), `LEFT JOIN` + `COALESCE`.
3. **Динамика специализаций** — число созданных моделей по месяцам в разрезе
   `pipeline_tag` (pivot + временны́е ряды): тренд на мультимодальность.
4. **Виральность против внедрения** — коэффициент `likes / downloads` по авторам:
   отделение «исследовательских любимчиков» от «рабочих утилит».
5. **Влияние лицензии на адопшн** — среднее и медианное число загрузок по
   категориям лицензий (boxplot), сравнение полностью открытых лицензий и
   открытых весов с ограничениями.

Запуск ноутбука:

```bash
uv run jupyter notebook notebooks/analytics.ipynb
# или конвертировать в HTML без интерактива
uv run jupyter nbconvert --to notebook --execute --inplace notebooks/analytics.ipynb
```

---

## Зависимости

`requests`, `SQLAlchemy 2.x`, `pandas`, `matplotlib`, `seaborn`, `jupyter`,
`ipykernel` (см. `pyproject.toml` / `uv.lock`).
