"""Zero-token health probes for the shared providers.

`probe(base_url, model, api_key)` does GET {base_url}/models and reports whether
the model is listed. On the Hugging Face router the model list is public, so a
separate whoami call checks that HF_TOKEN is actually valid.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def _get(url: str, api_key: str | None, timeout: float) -> dict:
    h = {"User-Agent": "instinct-models"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return json.loads(r.read().decode())


def hf_token_valid(token: str | None, timeout: float = 15) -> bool | str:
    if not token:
        return False
    try:
        return bool(_get("https://huggingface.co/api/whoami-v2", token, timeout).get("name"))
    except urllib.error.HTTPError as e:
        return False if e.code in (401, 403) else f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return f"unreachable: {getattr(e, 'reason', e)}"


def probe(base_url: str, model: str | None, api_key: str | None = None, timeout: float = 15) -> dict:
    """Never raises. ok=True means the server answered (and, on HF, the token is valid)."""
    try:
        data = _get(base_url.rstrip("/") + "/models", api_key, timeout)
    except urllib.error.HTTPError as e:
        return {"base_url": base_url, "ok": False, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return {"base_url": base_url, "ok": False, "error": str(getattr(e, "reason", e))}
    ids = [m.get("id", "") for m in data.get("data", [])]
    out = {"base_url": base_url, "ok": True, "model": model, "model_listed": model in ids,
           "models_available": len(ids)}
    if "huggingface.co" in base_url:
        out["token_valid"] = hf_token_valid(api_key, timeout)
        out["ok"] = out["token_valid"] is True
    return out
