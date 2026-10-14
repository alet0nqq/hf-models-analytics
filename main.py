# main.py
"""CLI-оркестратор ETL-пайплайна Hugging Face Hub -> SQLite.

Команды:
    init          — создать/инициализировать базу данных;
    collect       — первичный сбор топ-N моделей по downloads;
    refresh       — освежить метрики (downloads/likes) топ-N моделей;
    incremental   — подгрузить только новые модели (createdAt > последней в БД);
    all           — init + collect (полный первичный прогон);
    stats         — показать количество строк в таблицах.

Примеры:
    python main.py all --limit 3000
    python main.py refresh --limit 500
    python main.py incremental --limit 1000
"""
import argparse
import time

from sqlalchemy.orm import Session

from collector import fetch_models
from loader import db_stats, format_since, get_last_created, load_models
from models import engine, init_db


def _fetch_and_load(limit: int, since=None, label: str = "collect") -> None:
    started = time.time()
    print(f"[{label}] Сбор с HF API (limit={limit}, since={since}) ...")

    totals = {"processed": 0, "inserted": 0, "updated": 0}

    def on_page(page_records):
        stats = load_models(page_records, verbose=False)
        for key in totals:
            totals[key] += stats[key]
        print(f"  [{label}] страница сохранена: +{stats['inserted']} новых, "
              f"~{stats['updated']} обновлено (всего {totals['processed']})")

    try:
        fetch_models(limit, since=since, page_callback=on_page)
    except Exception as exc:  # обрыв сети: частичный прогресс уже в БД
        print(f"[{label}] Сбор прерван ({type(exc).__name__}), "
              f"но уже загруженные страницы сохранены.")
    print(f"[{label}] Записей обработано: {totals['processed']} "
          f"за {time.time() - started:.1f} c")


def cmd_init(_args) -> None:
    init_db()
    print("База данных инициализирована.")


def cmd_collect(args) -> None:
    init_db()
    _fetch_and_load(args.limit, since=None, label="collect")
    print("Статистика БД:", db_stats())


def cmd_refresh(args) -> None:
    init_db()
    _fetch_and_load(args.limit, since=None, label="refresh")
    print("Статистика БД:", db_stats())


def cmd_incremental(args) -> None:
    init_db()
    with Session(engine) as session:
        last = get_last_created(session)
    since = format_since(last)
    print(f"Последняя created_at в БД: {last} -> since={since}")
    _fetch_and_load(args.limit, since=since, label="incremental")
    print("Статистика БД:", db_stats())


def cmd_all(args) -> None:
    init_db()
    print("=== Шаг 1. Первичный сбор топ-моделей ===")
    _fetch_and_load(args.limit, since=None, label="collect")
    print("\n=== Готово. Статистика БД ===")
    print(db_stats())


def cmd_stats(_args) -> None:
    print("Статистика БД:", db_stats())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETL: Hugging Face Hub -> SQLite")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="инициализировать БД")
    p_init.set_defaults(func=cmd_init)

    p_collect = sub.add_parser("collect", help="первичный сбор топ-N по downloads")
    p_collect.add_argument("--limit", type=int, default=3000)
    p_collect.set_defaults(func=cmd_collect)

    p_refresh = sub.add_parser("refresh", help="освежить метрики топ-N моделей")
    p_refresh.add_argument("--limit", type=int, default=500)
    p_refresh.set_defaults(func=cmd_refresh)

    p_incr = sub.add_parser("incremental", help="подгрузить новые модели")
    p_incr.add_argument("--limit", type=int, default=1000)
    p_incr.set_defaults(func=cmd_incremental)

    p_all = sub.add_parser("all", help="init + первичный сбор")
    p_all.add_argument("--limit", type=int, default=3000)
    p_all.set_defaults(func=cmd_all)

    p_stats = sub.add_parser("stats", help="показать статистику БД")
    p_stats.set_defaults(func=cmd_stats)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
