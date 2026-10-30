"""Fetch Quran text, transliteration, and translation from Quran.com API v4."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from normalize import normalize_arabic
    from pipeline_common import ConfigError, load_config, resolve_path
except ModuleNotFoundError:
    from scripts.normalize import normalize_arabic
    from scripts.pipeline_common import ConfigError, load_config, resolve_path


_SUP_PATTERN = re.compile(r"<sup\b[^>]*>.*?</sup\s*>", re.IGNORECASE | re.DOTALL)
_TAG_PATTERN = re.compile(r"<\s*/?\s*([A-Za-z][\w:-]*)\b[^>]*>", re.IGNORECASE)
_SPACE_PATTERN = re.compile(r"\s+")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")


def clean_display_text(value: str, found_tags: set[str] | None = None) -> str:
    """Remove API markup and normalize presentation spacing without changing content."""
    if not isinstance(value, str):
        raise ConfigError("Displayed text must be a string")
    if found_tags is not None:
        found_tags.update(match.group(1).lower() for match in _TAG_PATTERN.finditer(value))
    value = _SUP_PATTERN.sub("", value)
    value = _TAG_PATTERN.sub(lambda match: match.group(0)[match.group(0).find(">") + 1:], value)
    value = _SPACE_PATTERN.sub(" ", value).strip()
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", value)


def _assert_clean_display_fields(item: dict[str, Any], label: str) -> None:
    for field in ("arabic", "translit", "english"):
        value = item.get(field)
        if not isinstance(value, str) or "<" in value or ">" in value:
            raise ConfigError(f"HTML delimiters remain in {label}.{field}")


def _get(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = session.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ConfigError(f"Quran.com request failed for {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Quran.com returned an invalid response for {url}")
    return data


def verify_resources(session: requests.Session, base_url: str, config: dict[str, Any]) -> None:
    resources = _get(session, f"{base_url}/resources/translations", {})["translations"]
    by_id = {item.get("id"): item for item in resources if isinstance(item, dict)}
    expected = {
        config["api"]["translation_id"]: "Saheeh International",
        config["api"]["transliteration_id"]: "Transliteration",
    }
    for resource_id, name in expected.items():
        resource = by_id.get(resource_id)
        if resource is None or resource.get("name") != name or resource.get("language_name") != "english":
            actual = resource.get("name") if resource else "missing"
            raise ConfigError(f"Translation resource {resource_id} is {actual!r}; expected {name!r}")


def _words(verse: dict[str, Any], found_tags: set[str] | None = None) -> list[dict[str, Any]]:
    result = []
    for word in verse.get("words", []):
        if word.get("char_type_name") != "word":
            continue
        arabic = word.get("text_uthmani")
        translit = (word.get("transliteration") or {}).get("text")
        if not isinstance(arabic, str) or not isinstance(translit, str):
            raise ConfigError(f"Missing word data for {verse.get('verse_key')}")
        result.append({
            "i": len(result),
            "arabic": arabic,
            "norm": normalize_arabic(arabic),
            "translit": clean_display_text(translit, found_tags),
        })
    return result


def _verse(
    verse: dict[str, Any],
    translation_id: int,
    transliteration_id: int,
    found_tags: set[str] | None = None,
) -> dict[str, Any]:
    words = _words(verse, found_tags)
    arabic = verse.get("text_uthmani", "").strip()
    translations = verse.get("translations", [])
    api_translit = next((item.get("text", "").strip() for item in translations
                         if item.get("resource_id") == transliteration_id), "")
    translation = next(
        (item.get("text", "").strip() for item in verse.get("translations", [])
         if item.get("resource_id") == translation_id), "")
    if not arabic or not api_translit or not translation:
        raise ConfigError(f"Incomplete Quran.com data for {verse.get('verse_key')}")
    translit = " ".join(word["translit"] for word in words)
    translation = clean_display_text(translation, found_tags)
    joined = " ".join(word["arabic"] for word in words)
    if joined != arabic and normalize_arabic(joined) != normalize_arabic(arabic):
        raise ConfigError(f"Word integrity mismatch for {verse.get('verse_key')}")
    result = {"arabic": arabic, "translit": translit, "english": translation, "words": words}
    _assert_clean_display_fields(result, verse.get("verse_key", "verse"))
    if joined != arabic:
        result["word_integrity_warning"] = "API word marks differ from verse text"
    return result


def fetch_text(
    surah: int,
    config: dict[str, Any],
    session: requests.Session | None = None,
    found_tags: set[str] | None = None,
) -> dict[str, Any]:
    client = session or requests.Session()
    base_url = config["api"]["base_url"].rstrip("/")
    verify_resources(client, base_url, config)
    chapter = _get(client, f"{base_url}/chapters/{surah}", {"language": "en"})["chapter"]
    verses: list[dict[str, Any]] = []
    page = 1
    while True:
        data = _get(client, f"{base_url}/verses/by_chapter/{surah}", {
            "words": "true",
            "translations": f'{config["api"]["translation_id"]},{config["api"]["transliteration_id"]}',
            "fields": "text_uthmani", "word_fields": "text_uthmani,transliteration",
            "per_page": 50, "page": page,
        })
        verses.extend(data.get("verses", []))
        pagination = data.get("pagination", {})
        if page >= pagination.get("total_pages", page):
            break
        page += 1
    ayahs = []
    for raw in verses:
        item = _verse(raw, config["api"]["translation_id"], config["api"]["transliteration_id"], found_tags)
        item["n"] = raw["verse_number"]
        ayahs.append(item)
    bismillah = None
    if surah not in (1, 9) and config["bismillah"]:
        opening = _get(client, f"{base_url}/verses/by_key/1:1", {
            "words": "true", "translations": f'{config["api"]["translation_id"]},{config["api"]["transliteration_id"]}',
            "fields": "text_uthmani", "word_fields": "text_uthmani,transliteration",
        })["verse"]
        bismillah = _verse(
            opening, config["api"]["translation_id"], config["api"]["transliteration_id"], found_tags
        )
    return {"surah": surah, "riwaya": config["api"]["riwaya"], "title_ar": chapter["name_arabic"],
            "title_en": chapter["translated_name"]["name"], "bismillah": bismillah, "ayahs": ayahs}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Quran.com text for one surah.")
    parser.add_argument("--surah", type=int, required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", help="Output text.json path; defaults to work/<surah>/text.json")
    args = parser.parse_args()
    try:
        if not 1 <= args.surah <= 114:
            raise ConfigError("--surah must be between 1 and 114")
        config = load_config(args.config)
        found_tags: set[str] = set()
        text = fetch_text(args.surah, config, found_tags=found_tags)
        output = Path(args.output) if args.output else resolve_path(config["paths"]["work_dir"]) / str(args.surah) / "text.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(text, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"HTML tags found: {', '.join(sorted(found_tags)) or '(none)'}")
        print(f"fetched surah {args.surah}: {len(text['ayahs'])} ayahs -> {output}")
        return 0
    except (ConfigError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())