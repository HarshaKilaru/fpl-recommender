import json, os, time
from typing import Callable, Any

def cache_json(path: str, ttl_seconds: int, loader: Callable[[], Any]) -> Any:
    """
    Tiny disk cache for JSON-able payloads.
    If the file exists and is fresher than ttl_seconds, return it.
    Otherwise call loader(), write the result, and return it.
    """
    try:
        if os.path.exists(path):
            age = time.time() - os.path.getmtime(path)
            if age < ttl_seconds:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
    except Exception:
        pass

    data = loader()

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        # best-effort cache; still return data
        pass

    return data
