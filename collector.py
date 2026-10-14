import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = "https://huggingface.co/api/models"
PAGE_SIZE = 1000
PAUSE = 0.5
TIMEOUT = 15


def make_session() -> requests.Session:
    """Сессия с ретраями и экспоненциальным бэкоффом (устойчивость к сбоям)."""
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def parse_model(raw):
    model_id = raw["id"]
    author = model_id.split("/")[0] if "/" in model_id else None

    tags = raw.get("tags", [])
    license_ = None
    for tag in tags:
        if tag.startswith("license:"):
            license_ = tag[len("license:"):]
            break

    return {
        "model_id": model_id,
        "author": author,
        "pipeline_tag": raw.get("pipeline_tag"),
        "downloads": raw.get("downloads"),
        "likes": raw.get("likes"),
        "license": license_,
        "created_at": raw.get("createdAt"),
        "tags": tags,
    }


def fetch_models(limit, since=None, page_callback=None, session=None):
    """Собирает до `limit` моделей с HF API.

    page_callback: если задан, вызывается после каждой страницы со списком
    новых записей этой страницы — позволяет сохранять данные инкрементально и
    не терять прогресс при обрыве соединения на следующих страницах.
    """
    session = session or make_session()
    result = []
    seen = set()
    url = URL
    params = {"limit": min(limit, PAGE_SIZE)}
    if since is not None:
        params["sort"] = "createdAt"
    else:
        params["sort"] = "downloads"
    params["direction"] = -1

    while url and len(result) < limit:
        response = session.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()

        page_records = []
        for raw in response.json():
            model = parse_model(raw)

            created = model["created_at"]
            if since is not None and created is not None and created <= since:
                if page_callback and page_records:
                    page_callback(page_records)
                return result

            if model["model_id"] in seen:
                continue
            seen.add(model["model_id"])
            result.append(model)
            page_records.append(model)

            if len(result) == limit:
                break

        if page_callback and page_records:
            page_callback(page_records)

        url = response.links.get("next", {}).get("url")
        params = None
        time.sleep(PAUSE)

    return result


if __name__ == "__main__":
    models = fetch_models(20)
    print(len(models))
    print(models[0])
    print(models[1])

    fresh = fetch_models(50, since="2026-10-01")
    print(len(fresh), all(m["created_at"] > "2026-10-01" for m in fresh))
