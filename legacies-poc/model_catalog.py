"""What models this machine can actually run, discovered rather than hardcoded.

The panel's Model field used to be a fixed list of two, written out in four
files. Installing a third model meant editing all of them, and forgetting one
produced a 422 with no explanation. Instead the list is assembled here, from
four sources:

  builtin    The Typhoon fine-tunes this project was built around. Always
             listed, downloaded on first use.
  local      models.local.json, a plain {"key": "org/repo"} map. Add a model
             without touching Python.
  cache      Whisper models already downloaded into the Hugging Face cache.
             This is what makes `hf download <repo>` show up by itself.
  converted  Directories under models/, which reveal a model whose weights
             have been converted for openvino / ctranslate2 / whispercpp —
             even one whose original repo isn't known here.

Discovery is filesystem-only: no network, so it stays cheap enough to call on
every request.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
MODELS_DIR = PROJECT_DIR / "models"
LOCAL_MODELS_FILE = PROJECT_DIR / "models.local.json"

# The models this project was built to test. Kept as a constant rather than
# discovered, so a fresh checkout offers them before anything is downloaded.
BUILTIN_MODELS = {
    "turbo": "typhoon-ai/typhoon-whisper-turbo",
    "large-v3": "typhoon-ai/typhoon-whisper-large-v3",
}

# Runtime families that have their own weight format on disk, as the directory
# prefix convert_model.py writes. Both OpenVINO devices share one directory:
# the IR is identical and the device is chosen at load time.
CONVERTED_FAMILIES = ("openvino", "ctranslate2", "whispercpp")

# A model key becomes a path component in models/<family>-<key>, so it cannot
# be allowed to contain separators or dots that would climb out of models/.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_safe_key(key: str) -> bool:
    """Keys reach the filesystem, and now come from disk rather than a literal."""
    return bool(_SAFE_KEY.match(key)) and ".." not in key


def converted_dir(runtime: str, model_key: str) -> Path:
    """Where convert_model.py puts the converted weights for this combination."""
    if not is_safe_key(model_key):
        raise ValueError(f"Unsafe model key {model_key!r}")
    family = "openvino" if runtime.startswith("openvino") else runtime
    return MODELS_DIR / f"{family}-{model_key}"


def _key_from_repo(repo: str) -> str:
    """A short, filesystem-safe key for a Hugging Face repo id."""
    name = repo.split("/")[-1]
    key = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-.")
    return key or re.sub(r"[^A-Za-z0-9._-]", "-", repo)


def _read_local_file() -> dict[str, str]:
    if not LOCAL_MODELS_FILE.exists():
        return {}
    try:
        data = json.loads(LOCAL_MODELS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A malformed file must not take the panel down — the rest of the
        # catalogue is still perfectly usable.
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in data.items()
        if is_safe_key(str(k)) and isinstance(v, str) and v.strip()
    }


def _scan_hf_cache() -> dict[str, dict]:
    """Whisper models already sitting in the Hugging Face cache."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except ImportError:
        return {}

    found: dict[str, dict] = {}
    cache = Path(HF_HUB_CACHE)
    if not cache.is_dir():
        return {}

    for entry in sorted(cache.glob("models--*")):
        parts = entry.name.split("--")[1:]
        if len(parts) < 2:
            continue
        repo = "/".join(["--".join(parts[:-1]), parts[-1]])

        snapshots = sorted((entry / "snapshots").glob("*")) if (entry / "snapshots").is_dir() else []
        config = next((s / "config.json" for s in snapshots if (s / "config.json").is_file()), None)
        if config is None:
            continue  # e.g. a GGML-only repo: real, but not loadable by transformers

        try:
            cfg = json.loads(config.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if cfg.get("model_type") != "whisper":
            continue  # the cache holds all sorts of models; only Whisper runs here

        snapshot = config.parent
        multilingual = None
        gen = snapshot / "generation_config.json"
        if gen.is_file():
            try:
                multilingual = json.loads(gen.read_text(encoding="utf-8")).get("is_multilingual")
            except (json.JSONDecodeError, OSError):
                pass

        key = _key_from_repo(repo)
        if not is_safe_key(key):
            continue
        found[key] = {
            "repo": repo,
            "multilingual": multilingual,
            "decoder_layers": cfg.get("decoder_layers"),
        }
    return found


def _scan_converted() -> dict[str, list[str]]:
    """Model keys that have converted weights on disk, by runtime family."""
    found: dict[str, list[str]] = {}
    if not MODELS_DIR.is_dir():
        return found
    for entry in sorted(MODELS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        for family in CONVERTED_FAMILIES:
            prefix = f"{family}-"
            if entry.name.startswith(prefix):
                key = entry.name[len(prefix):]
                if is_safe_key(key):
                    found.setdefault(key, []).append(family)
                break
    return found


def discover() -> dict[str, dict]:
    """Every model this machine can offer, keyed by model key.

    Each entry carries where it came from and what is known about it. `repo`
    is None only for a model found solely as converted weights — runnable by
    that runtime, but not by `pytorch`, which needs the original repo.
    """
    models: dict[str, dict] = {}

    def add(key: str, repo: str | None, source: str, **extra) -> None:
        if not is_safe_key(key):
            return
        entry = models.setdefault(
            key,
            {
                "key": key,
                "repo": None,
                "sources": [],
                "converted": [],
                "multilingual": None,
                "decoder_layers": None,
            },
        )
        if repo and not entry["repo"]:
            entry["repo"] = repo
        if source not in entry["sources"]:
            entry["sources"].append(source)
        for field, value in extra.items():
            if value is not None and entry.get(field) is None:
                entry[field] = value

    for key, repo in BUILTIN_MODELS.items():
        add(key, repo, "builtin")

    # A local entry may point an existing key at a different repo, so it is
    # applied by overwriting rather than through add()'s first-wins rule.
    for key, repo in _read_local_file().items():
        add(key, repo, "local")
        models[key]["repo"] = repo

    # A cached repo that a builtin or local key already points at is the same
    # model, not a second one — listing it twice would offer two names for one
    # download and convert it twice into two directories. Merge what the cache
    # knows (multilingual, decoder count) into the existing key instead.
    by_repo = {entry["repo"]: key for key, entry in models.items() if entry["repo"]}
    for key, info in _scan_hf_cache().items():
        target = by_repo.get(info["repo"], key)
        add(
            target,
            info["repo"],
            "cache",
            multilingual=info.get("multilingual"),
            decoder_layers=info.get("decoder_layers"),
        )

    for key, families in _scan_converted().items():
        add(key, None, "converted")
        models[key]["converted"] = families

    return dict(sorted(models.items()))


def model_repos() -> dict[str, str]:
    """Discovered key -> repo, for everything that has a repo. CLI choices."""
    return {k: v["repo"] for k, v in discover().items() if v["repo"]}


def resolve_repo(key: str) -> str:
    """The Hugging Face repo for a key, or a message explaining why there isn't one."""
    entry = discover().get(key)
    if entry is None:
        raise KeyError(f"Unknown model {key!r}")
    if not entry["repo"]:
        raise KeyError(
            f"Model {key!r} was found only as converted weights "
            f"({', '.join(entry['converted']) or 'none'}), so its original repo isn't known "
            f"here. Add it to {LOCAL_MODELS_FILE.name} to run it on pytorch."
        )
    return entry["repo"]


def main() -> None:
    for key, entry in discover().items():
        repo = entry["repo"] or "(converted weights only)"
        bits = [f"from {'+'.join(entry['sources'])}"]
        if entry["converted"]:
            bits.append(f"converted for {'+'.join(entry['converted'])}")
        if entry["multilingual"] is False:
            bits.append("ENGLISH-ONLY")
        print(f"  {key:28} {repo:46} {'; '.join(bits)}")


if __name__ == "__main__":
    main()
