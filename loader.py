# loader.py
"""Загрузка собранных записей в SQLite: идемпотентный upsert + нормализация.

Ключевые идеи:
    * справочники authors/licenses наполняются по паттерну get-or-create с
      кэшем в оперативной памяти (минимум транзакций к диску);
    * модели пишутся одним `INSERT ... ON CONFLICT(model_id) DO UPDATE`,
      поэтому повторные запуски не плодят дубли, а лишь освежают
      downloads/likes/pipeline_tag/created_at (инкрементальная подгрузка);
    * категория лицензии вычисляется один раз и сохраняется в справочнике;
    * коммиты выполняются пачками (batch_size).
"""
import json
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from models import Author, License, Model, engine

# ---------------------------------------------------------------------------
# Классификация лицензий
# ---------------------------------------------------------------------------
_PERMISSIVE = {
    "apache-2.0", "apache-1.1", "mit", "bsd-2-clause", "bsd-3-clause", "0bsd",
    "unlicense", "cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0", "cc-by-3.0", "zlib",
    "isc", "artistic-2.0", "python-2.0", "bsd", "mit", "mpl", "eupl-1.1",
}
_RESTRICTED_EXACT = {
    "gemma", "gemma2", "gemma3", "deepseek", "qwen", "qwen-research",
    "bigscience-bloom-rail-1.0", "bigcode-openrail-m", "bigcode-openrail++-m",
    "creativeml-openrail-m", "creativeml-openrail-m-plus", "openrail++",
    "openrail", "rail", "falconn", "nvidia-open-model-license",
}


def classify_license(name: Optional[str]) -> str:
    """Возвращает категорию лицензии: open / copyleft / restricted /
    noncommercial / other."""
    if not name:
        return "other"
    n = name.strip().lower()
    if n in {"", "other", "unknown", "none", "null"}:
        return "other"
    # некоммерческие — раньше пермиссивных, т.к. "cc-by-nc" содержит "cc-by"
    if n.startswith("cc-by-nc") or "non-commercial" in n or "noncommercial" in n:
        return "noncommercial"
    # открытые веса с ограничениями (Llama, Gemma, RAIL, DeepSeek, Qwen ...)
    if "llama" in n or n.startswith("gemma") or "deepseek" in n or "rail" in n \
            or n in _RESTRICTED_EXACT:
        return "restricted"
    # копилефт
    if n.startswith(("gpl", "lgpl", "agpl", "mpl", "eupl")):
        return "copyleft"
    # пермиссивные (полностью открытые коммерческие)
    if n in _PERMISSIVE or n.startswith(("apache", "mit", "bsd", "cc-by", "cc0")):
        return "open"
    return "other"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def parse_dt(raw: Optional[str]) -> Optional[datetime]:
    """Парсит ISO-строку HF ('2024-05-22T18:42:11.000Z') в наивный UTC-datetime."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def format_since(dt: Optional[datetime]) -> Optional[str]:
    """Форматирует datetime в строку, совместимую с createdAt из HF API."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def get_or_create_author(session: Session, cache: Dict[str, int],
                         name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    clean = name.strip()
    if not clean:
        return None
    if clean in cache:
        return cache[clean]
    author = session.scalar(select(Author).where(Author.name == clean))
    if author is None:
        author = Author(name=clean)
        session.add(author)
        session.flush()
    cache[clean] = author.id
    return author.id


def get_or_create_license(session: Session, cache: Dict[str, int],
                          name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    clean = name.strip()
    if not clean:
        return None
    if clean in cache:
        return cache[clean]
    lic = session.scalar(select(License).where(License.name == clean))
    if lic is None:
        lic = License(name=clean, category=classify_license(clean))
        session.add(lic)
        session.flush()
    cache[clean] = lic.id
    return lic.id


# ---------------------------------------------------------------------------
# Основная загрузка
# ---------------------------------------------------------------------------
def load_models(records: Iterable[dict], batch_size: int = 500,
                verbose: bool = True) -> Dict[str, int]:
    """Записывает список словарей (из collector.parse_model) в БД.

    Возвращает статистику {'processed', 'inserted', 'updated'}.
    """
    records: List[dict] = list(records)
    processed = inserted = updated = 0

    with Session(engine) as session:
        author_cache: Dict[str, int] = dict(
            session.execute(select(Author.name, Author.id)).all()
        )
        license_cache: Dict[str, int] = dict(
            session.execute(select(License.name, License.id)).all()
        )
        existing = set(session.scalars(select(Model.model_id)).all())

        for rec in records:
            model_id = rec["model_id"]
            author_id = get_or_create_author(session, author_cache, rec.get("author"))
            license_id = get_or_create_license(session, license_cache, rec.get("license"))

            stmt = sqlite_insert(Model).values(
                model_id=model_id,
                author_id=author_id,
                license_id=license_id,
                pipeline_tag=rec.get("pipeline_tag"),
                downloads=int(rec.get("downloads") or 0),
                likes=int(rec.get("likes") or 0),
                created_at=parse_dt(rec.get("created_at")),
                tags_json=json.dumps(rec.get("tags") or [], ensure_ascii=False),
                updated_at=func.now(),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["model_id"],
                set_={
                    "author_id": stmt.excluded.author_id,
                    "license_id": stmt.excluded.license_id,
                    "pipeline_tag": stmt.excluded.pipeline_tag,
                    "downloads": stmt.excluded.downloads,
                    "likes": stmt.excluded.likes,
                    "created_at": stmt.excluded.created_at,
                    "tags_json": stmt.excluded.tags_json,
                    "updated_at": func.now(),
                },
            )
            session.execute(stmt)

            if model_id in existing:
                updated += 1
            else:
                inserted += 1
                existing.add(model_id)
            processed += 1

            if processed % batch_size == 0:
                session.commit()
                if verbose:
                    print(f"  ...загружено {processed}/{len(records)} "
                          f"(+{inserted} новых, ~{updated} обновлено)")

        session.commit()

    stats = {"processed": processed, "inserted": inserted, "updated": updated}
    if verbose:
        print(f"[loader] Итого: {processed} обработано, "
              f"{inserted} новых, {updated} обновлено.")
    return stats


def get_last_created(session: Session) -> Optional[datetime]:
    """Максимальный created_at в БД (для инкрементальной подгрузки)."""
    return session.scalar(select(func.max(Model.created_at)))


def db_stats() -> Dict[str, int]:
    with Session(engine) as session:
        return {
            "models": session.scalar(select(func.count(Model.id))) or 0,
            "authors": session.scalar(select(func.count(Author.id))) or 0,
            "licenses": session.scalar(select(func.count(License.id))) or 0,
        }


if __name__ == "__main__":
    from models import init_db
    init_db()
    print("Классификация лицензий (пример):")
    for name in ["apache-2.0", "mit", "llama3.1", "gemma", "cc-by-nc-4.0",
                 "gpl-3.0", "openrail++", "other", None]:
        print(f"  {str(name):20s} -> {classify_license(name)}")
    print("Статистика БД:", db_stats())
