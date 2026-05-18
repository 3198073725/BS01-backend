from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from django.conf import settings

from apps.configs.utils import get_system_setting
from apps.content.comment_moderation_rules import (
    COMMENT_ASCII_SUBSTITUTIONS,
    COMMENT_PATTERN_RULES,
    COMMENT_TEXT_CANONICAL_RULES,
    DEFAULT_COMMENT_BLOCKED_KEYWORDS,
)
from apps.content.models import AuditLog


ZHIPU_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_DEFAULT_MODEL = "moderation"
DEFAULT_BLOCKED_CATEGORIES = (
    "porn",
    "abuse",
    "violence",
    "contraband",
    "politics",
    "crime",
)


def _normalize_text(value: str) -> str:
    return (value or "").strip().lower()


def _parse_rule_lines(raw: str | None) -> list[str]:
    if not raw:
        return []
    items: list[str] = []
    for chunk in str(raw).replace(",", "\n").splitlines():
        line = str(chunk or "").strip()
        if line:
            items.append(line)
    return items


def _parse_canonical_rules(raw: str | None) -> list[tuple[str, str]]:
    rules: list[tuple[str, str]] = []
    for line in _parse_rule_lines(raw):
        if "=" not in line:
            continue
        src, dst = line.split("=", 1)
        left = str(src).strip()
        right = str(dst).strip()
        if left and right:
            rules.append((left, right))
    return rules


def _parse_pattern_rules(raw: str | None) -> list[tuple[re.Pattern[str], str]]:
    rules: list[tuple[re.Pattern[str], str]] = []
    for line in _parse_rule_lines(raw):
        if "=" not in line:
            continue
        expr, label = line.split("=", 1)
        pattern_text = str(expr).strip()
        pattern_label = str(label).strip()
        if not pattern_text or not pattern_label:
            continue
        try:
            rules.append((re.compile(pattern_text, re.IGNORECASE), pattern_label))
        except re.error:
            continue
    return rules


def _compact_text(value: str) -> str:
    text = _normalize_text(value)
    text = text.translate(COMMENT_ASCII_SUBSTITUTIONS)
    text = re.sub(r"[\s`~!@#$%^&*()\-_=+\[\]{}\\|;:'\",<.>/?，。！？、；：（）【】《》“”‘’…·]+", "", text)
    return text


def _canonicalize_comment_text(value: str, canonical_rules: Iterable[tuple[str, str]] | None = None) -> str:
    text = _compact_text(value)
    if not text:
        return ""
    for src, dst in canonical_rules or COMMENT_TEXT_CANONICAL_RULES:
        text = text.replace(src, dst)
    return text


def _parse_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    items: list[str] = []
    for chunk in str(raw).replace(",", "\n").splitlines():
        word = _normalize_text(chunk)
        if word:
            items.append(word)
    return list(dict.fromkeys(items))


def _resolve_keyword_source(raw: str | None, fallback_keywords: Iterable[str] = ()) -> str:
    text = str(raw or "").strip()
    if text:
        return text
    return ",".join(str(item).strip() for item in fallback_keywords if str(item).strip())


def _comment_canonical_rules() -> list[tuple[str, str]]:
    return [*COMMENT_TEXT_CANONICAL_RULES, *_parse_canonical_rules(get_system_setting("COMMENT_CANONICAL_RULES", ""))]


def _comment_pattern_rules() -> list[tuple[re.Pattern[str], str]]:
    return [*COMMENT_PATTERN_RULES, *_parse_pattern_rules(get_system_setting("COMMENT_PATTERN_RULES", ""))]


def _parse_csv(raw: str | None, default: Iterable[str]) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return list(default)
    items = [part.strip() for part in text.replace("\n", ",").split(",")]
    return [item for item in items if item]


def _truthy(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_value(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _api_key() -> str:
    value = (
        get_system_setting("ZHIPU_API_KEY", None)
        or os.getenv("ZHIPU_API_KEY")
        or getattr(settings, "ZHIPU_API_KEY", None)
        or ""
    )
    return str(value).strip()


def _api_base_url() -> str:
    value = (
        get_system_setting("ZHIPU_BASE_URL", None)
        or os.getenv("ZHIPU_BASE_URL")
        or getattr(settings, "ZHIPU_BASE_URL", None)
        or ZHIPU_DEFAULT_BASE_URL
    )
    return str(value).rstrip("/")


def _moderation_model() -> str:
    value = get_system_setting("ZHIPU_MODERATION_MODEL", ZHIPU_DEFAULT_MODEL) or ZHIPU_DEFAULT_MODEL
    return str(value).strip() or ZHIPU_DEFAULT_MODEL


def _fail_closed() -> bool:
    return _truthy(get_system_setting("ZHIPU_MODERATION_FAIL_CLOSED", False), default=False)


def _timeout_seconds() -> int:
    return _int_value(get_system_setting("ZHIPU_MODERATION_TIMEOUT_SECONDS", 15), 15)


def _blocked_categories() -> list[str]:
    return _parse_csv(
        get_system_setting("ZHIPU_MODERATION_BLOCKED_CATEGORIES", ""),
        DEFAULT_BLOCKED_CATEGORIES,
    )


def _use_zhipu() -> bool:
    if not _truthy(get_system_setting("ZHIPU_MODERATION_ENABLED", True), default=True):
        return False
    return bool(_api_key())


def _site_url() -> str:
    value = (
        get_system_setting("MODERATION_MEDIA_PUBLIC_BASE_URL", None)
        or get_system_setting("SITE_URL", None)
        or os.getenv("SITE_URL")
        or getattr(settings, "SITE_URL", None)
        or ""
    )
    return str(value or "").rstrip("/")


def _lookup_config_value(
    key: str,
    default=None,
    overrides: dict | None = None,
    env_key: str | None = None,
    settings_key: str | None = None,
):
    if overrides and key in overrides and overrides[key] not in {None, ""}:
        return overrides[key]
    env_name = env_key or key
    settings_name = settings_key or key
    value = (
        get_system_setting(key, None)
        or os.getenv(env_name)
        or getattr(settings, settings_name, None)
        or default
    )
    return value


def _is_public_host(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False
    if not host or host in {"localhost"}:
        return False
    if re.match(r"^127\.", host):
        return False
    if re.match(r"^10\.", host):
        return False
    if re.match(r"^192\.168\.", host):
        return False
    if re.match(r"^172\.(1[6-9]|2\d|3[0-1])\.", host):
        return False
    return True


def _public_media_url(rel: str) -> str | None:
    site = _site_url()
    media = str(getattr(settings, "MEDIA_URL", "/media") or "/media").rstrip("/")
    rel = str(rel or "").lstrip("/")
    if not site or not rel:
        return None
    if not _is_public_host(site):
        return None
    if media.startswith("http://") or media.startswith("https://"):
        return f"{media}/{rel}"
    if media.startswith("/"):
        return f"{site}{media}/{rel}"
    return f"{site}/{media}/{rel}"


@dataclass
class ModerationResult:
    allowed: bool
    matched_keywords: list[str]
    matched_details: list[dict[str, str]]
    source: str
    flagged_categories: list[str]
    category_scores: dict[str, float]
    raw_response: dict | None
    error: str | None = None


def _local_text_review(
    texts: Iterable[str],
    raw_keywords: str | None,
    canonical_rules: Iterable[tuple[str, str]] | None = None,
    pattern_rules: Iterable[tuple[re.Pattern[str], str]] | None = None,
) -> ModerationResult:
    keywords = _parse_keywords(raw_keywords)
    if not keywords:
        return ModerationResult(True, [], [], "local-keywords", [], {}, None)
    normalized_parts = [_normalize_text(text) for text in texts if text]
    haystack = "\n".join(normalized_parts).strip()
    if not haystack:
        return ModerationResult(True, [], [], "local-keywords", [], {}, None)
    active_canonical_rules = list(canonical_rules or [])
    compact_haystack = _canonicalize_comment_text("".join(normalized_parts), active_canonical_rules)
    matched = []
    matched_details: list[dict[str, str]] = []
    for word in keywords:
        normalized_word = _normalize_text(word)
        compact_word = _canonicalize_comment_text(word, active_canonical_rules)
        if normalized_word and normalized_word in haystack:
            matched.append(word)
            matched_details.append({
                "type": "keyword",
                "label": word,
                "matched_text": word,
            })
            continue
        if compact_word and compact_word in compact_haystack:
            matched_text = word
            for text in texts:
                normalized_text = _normalize_text(text)
                compact_text = _canonicalize_comment_text(text, active_canonical_rules)
                if not normalized_text or compact_word not in compact_text:
                    continue
                for src, dst in active_canonical_rules:
                    if _canonicalize_comment_text(dst, active_canonical_rules) != compact_word:
                        continue
                    normalized_src = _normalize_text(src)
                    if normalized_src and normalized_src in normalized_text:
                        matched_text = src
                        break
                    compact_src = _compact_text(src)
                    if compact_src and compact_src in _compact_text(text):
                        matched_text = src
                        break
                if matched_text != word:
                    break
            matched.append(word)
            matched_details.append({
                "type": "canonical",
                "label": word,
                "matched_text": matched_text,
            })
    for pattern, label in pattern_rules or ():
        if pattern.search(haystack) and label not in matched:
            matched.append(label)
            matched_details.append({
                "type": "pattern",
                "label": label,
                "matched_text": pattern.pattern,
            })
    return ModerationResult(not matched, matched, matched_details, "local-keywords", [], {}, None)


def _post_zhipu_moderation(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=f"{_api_base_url()}/moderations",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_key()}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_zhipu_result(data: dict) -> ModerationResult:
    rows = (data or {}).get("result_list") or []
    flagged: list[str] = []
    scores: dict[str, float] = {}
    for item in rows:
        level = str((item or {}).get("risk_level") or "").upper()
        risks = [str(x) for x in ((item or {}).get("risk_type") or []) if str(x).strip()]
        if level in {"REVIEW", "REJECT"}:
            flagged.extend(risks or [level.lower()])
    flagged = list(dict.fromkeys(flagged))

    allowed = not flagged
    custom_categories = _blocked_categories()
    if custom_categories and flagged:
        allowed = not any(risk in custom_categories for risk in flagged)

    return ModerationResult(
        allowed=allowed,
        matched_keywords=[],
        matched_details=[],
        source="zhipu-moderation",
        flagged_categories=flagged,
        category_scores=scores,
        raw_response=data,
    )


def _review_zhipu_input(payload_input) -> ModerationResult:
    if not payload_input:
        return ModerationResult(True, [], [], "zhipu-moderation", [], {}, None)
    try:
        data = _post_zhipu_moderation({
            "model": _moderation_model(),
            "input": payload_input,
        })
        return _extract_zhipu_result(data)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:400]
        except Exception:
            detail = str(exc)
        return ModerationResult(
            allowed=not _fail_closed(),
            matched_keywords=[],
            matched_details=[],
            source="zhipu-moderation",
            flagged_categories=["service_error"] if _fail_closed() else [],
            category_scores={},
            raw_response=None,
            error=detail or str(exc),
        )
    except Exception as exc:
        return ModerationResult(
            allowed=not _fail_closed(),
            matched_keywords=[],
            matched_details=[],
            source="zhipu-moderation",
            flagged_categories=["service_error"] if _fail_closed() else [],
            category_scores={},
            raw_response=None,
            error=str(exc)[:400],
        )


def _merge_results(primary: ModerationResult, fallback: ModerationResult | None = None) -> ModerationResult:
    if not primary.allowed:
        return primary
    if fallback and not fallback.allowed:
        return fallback
    if primary.error and fallback:
        return fallback
    return primary


def review_comment_text(content: str) -> ModerationResult:
    if not _truthy(get_system_setting("AUTO_MODERATION_ENABLED", True), default=True):
        return ModerationResult(True, [], [], "disabled", [], {}, None)
    if not _truthy(get_system_setting("COMMENT_AUTOMOD_ENABLED", True), default=True):
        return ModerationResult(True, [], [], "disabled", [], {}, None)

    local_result = _local_text_review(
        [content],
        _resolve_keyword_source(
            get_system_setting("COMMENT_BLOCKED_KEYWORDS", ""),
            DEFAULT_COMMENT_BLOCKED_KEYWORDS,
        ),
        canonical_rules=_comment_canonical_rules(),
        pattern_rules=_comment_pattern_rules(),
    )
    if not local_result.allowed:
        return local_result
    if not _use_zhipu():
        return local_result
    remote_result = _review_zhipu_input(str(content or "")[:2000])
    return _merge_results(remote_result, local_result)


def review_video_text(title: str, description: str, filename: str = "") -> ModerationResult:
    if not _truthy(get_system_setting("AUTO_MODERATION_ENABLED", True), default=True):
        return ModerationResult(True, [], [], "disabled", [], {}, None)
    if not _truthy(get_system_setting("VIDEO_AUTOMOD_ENABLED", True), default=True):
        return ModerationResult(True, [], [], "disabled", [], {}, None)

    local_result = _local_text_review(
        [title, description, filename],
        get_system_setting("VIDEO_BLOCKED_KEYWORDS", ""),
    )
    if not local_result.allowed:
        return local_result
    if not _use_zhipu():
        return local_result
    text = "\n".join(part for part in [title, description, filename] if str(part or "").strip())[:2000]
    remote_result = _review_zhipu_input(text)
    return _merge_results(remote_result, local_result)


def review_video_media(title: str, description: str, image_paths: Iterable[str], video_rel: str = "") -> ModerationResult:
    if not _truthy(get_system_setting("AUTO_MODERATION_ENABLED", True), default=True):
        return ModerationResult(True, [], [], "disabled", [], {}, None)
    if not _truthy(get_system_setting("VIDEO_AUTOMOD_ENABLED", True), default=True):
        return ModerationResult(True, [], [], "disabled", [], {}, None)
    if not _use_zhipu():
        return ModerationResult(True, [], [], "disabled", [], {}, None)

    payload_input: list[dict] = []
    if video_rel:
        video_url = _public_media_url(video_rel)
        if video_url:
            payload_input.append({"type": "video", "url": video_url})
    for path in image_paths:
        rel = os.path.relpath(path, settings.MEDIA_ROOT).replace("\\", "/")
        image_url = _public_media_url(rel)
        if image_url:
            payload_input.append({"type": "image", "url": image_url})
    if not payload_input:
        return ModerationResult(
            allowed=not _fail_closed(),
            matched_keywords=[],
            matched_details=[],
            source="zhipu-moderation",
            flagged_categories=["media_url_unavailable"] if _fail_closed() else [],
            category_scores={},
            raw_response=None,
            error="media_public_url_unavailable",
        )
    return _review_zhipu_input(payload_input)


def get_automod_reject_message(default: str = "内容未通过自动质控，请修改后重试") -> str:
    value = str(get_system_setting("AUTOMOD_REJECT_MESSAGE", default) or "").strip()
    return value or default


def log_automod_block(
    *,
    actor=None,
    target_type: str | None = None,
    target_id=None,
    scenario: str,
    matched_keywords: list[str],
    matched_details: list[dict[str, str]] | None = None,
    source: str = "local-keywords",
    flagged_categories: list[str] | None = None,
    category_scores: dict[str, float] | None = None,
    error: str | None = None,
) -> None:
    try:
        AuditLog.objects.create(
            actor=actor,
            verb="content.automod.blocked",
            target_type=target_type,
            target_id=target_id,
            meta={
                "scenario": scenario,
                "source": source,
                "matched_keywords": matched_keywords[:20],
                "matched_details": list(matched_details or [])[:20],
                "flagged_categories": list(flagged_categories or [])[:20],
                "category_scores": category_scores or {},
                "error": error or "",
            },
        )
    except Exception:
        pass


def check_zhipu_moderation(overrides: dict | None = None) -> dict:
    api_key = str(_lookup_config_value("ZHIPU_API_KEY", "", overrides)).strip()
    base_url = str(_lookup_config_value("ZHIPU_BASE_URL", ZHIPU_DEFAULT_BASE_URL, overrides)).rstrip("/")
    model = str(_lookup_config_value("ZHIPU_MODERATION_MODEL", ZHIPU_DEFAULT_MODEL, overrides)).strip() or ZHIPU_DEFAULT_MODEL
    timeout = _int_value(_lookup_config_value("ZHIPU_MODERATION_TIMEOUT_SECONDS", 15, overrides), 15)
    media_base = str(_lookup_config_value("MODERATION_MEDIA_PUBLIC_BASE_URL", _site_url(), overrides) or "").rstrip("/")

    if not api_key:
        return {
            "ok": False,
            "configured": False,
            "message": "未配置 ZHIPU_API_KEY",
            "base_url": base_url,
            "model": model,
            "media_public_base_url": media_base,
            "media_public_url_usable": bool(media_base and _is_public_host(media_base)),
        }

    payload = {
        "model": model,
        "input": "这是一次后台自动质控配置自检，请仅用于验证审核接口连通性。",
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=f"{base_url}/moderations",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parsed = _extract_zhipu_result(data)
        return {
            "ok": True,
            "configured": True,
            "reachable": True,
            "message": "智谱审核接口调用成功",
            "base_url": base_url,
            "model": model,
            "media_public_base_url": media_base,
            "media_public_url_usable": bool(media_base and _is_public_host(media_base)),
            "result": {
                "allowed": parsed.allowed,
                "source": parsed.source,
                "flagged_categories": parsed.flagged_categories,
            },
        }
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:
            detail = str(exc)
        return {
            "ok": False,
            "configured": True,
            "reachable": False,
            "message": "智谱审核接口调用失败",
            "error": detail or str(exc),
            "base_url": base_url,
            "model": model,
            "media_public_base_url": media_base,
            "media_public_url_usable": bool(media_base and _is_public_host(media_base)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "reachable": False,
            "message": "智谱审核接口调用失败",
            "error": str(exc)[:500],
            "base_url": base_url,
            "model": model,
            "media_public_base_url": media_base,
            "media_public_url_usable": bool(media_base and _is_public_host(media_base)),
        }
