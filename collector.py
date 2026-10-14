import time
import requests

URL = "https://huggingface.co/api/models"
PAGE_SIZE = 1000
PAUSE = 0.5
TIMEOUT = 10


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


def fetch_models(limit, since=None):
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
        response = requests.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()

        for raw in response.json():
            model = parse_model(raw)

            created = model["created_at"]
            if since is not None and created is not None and created <= since:
                return result

            if model["model_id"] in seen:
                continue
            seen.add(model["model_id"])
            result.append(model)

            if len(result) == limit:
                break

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