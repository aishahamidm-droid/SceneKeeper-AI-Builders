"""Bounded Gemini retries shared by extraction and continuity reasoning."""

import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from google.genai import errors

logger = logging.getLogger(__name__)
QUOTA_MESSAGE = "Gemini is temporarily at its request limit. Please try again shortly."


class GeminiQuotaError(RuntimeError):
    """A quota failure safe to display in the application."""


def _seconds(value):
    try:
        if isinstance(value, dict):
            result = float(value.get("seconds", 0)) + float(value.get("nanos", 0)) / 1e9
        else:
            result = float(str(value).removesuffix("s"))
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def _quota_details(exc):
    payload = getattr(exc, "details", {})
    if not isinstance(payload, dict):
        payload = {}
    payload = payload.get("error", payload)
    details = payload.get("details", []) if isinstance(payload, dict) else []
    delays, quota_ids = [], []
    exhausted = False
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            delay = _seconds(detail.get("retryDelay"))
            if delay is not None:
                delays.append(delay)
        if str(detail.get("@type", "")).endswith("QuotaFailure"):
            for violation in detail.get("violations", []):
                if not isinstance(violation, dict):
                    continue
                quota_id = str(violation.get("quotaId", ""))
                quota_ids.append(quota_id)
                quota_name = (quota_id + str(violation.get("quotaMetric", ""))).lower()
                exhausted |= any(marker in quota_name for marker in (
                    "perday", "per_day", "permonth", "per_month"
                )) or str(violation.get("quotaValue")) == "0"
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("Retry-After")
    if retry_after is not None:
        delay = _seconds(retry_after)
        if delay is None:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                delay = max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        if delay is not None:
            delays.append(delay)
    return max(delays, default=0), exhausted, quota_ids


def generate_content_with_retry(client, *, max_attempts=3, initial_delay=2,
                                max_wait=60, **kwargs):
    """Retry only the Gemini request, never database writes or an entire audit.

    Three attempts and at most 60 seconds of total waiting by default. Never
    shorten a server-provided delay to fit the budget; surface the quota instead.
    """
    if max_attempts < 1 or initial_delay < 0 or max_wait < 0:
        raise ValueError("Invalid Gemini retry limits")
    waited = 0
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(**kwargs)
        except (errors.APIError, httpx.TransportError) as exc:
            code = getattr(exc, "code", None)
            quota = code == 429 or getattr(exc, "status", None) == "RESOURCE_EXHAUSTED"
            server_delay, exhausted, quota_ids = _quota_details(exc)
            wait = max(server_delay, initial_delay * 2 ** (attempt - 1))
            transient = quota or code in (408, 500, 502, 503, 504) or isinstance(exc, httpx.TransportError)
            retry = transient and not exhausted and attempt < max_attempts and waited + wait <= max_wait
            # Keep operational evidence in server logs, not captured stdout.
            # Do not log raw API payloads, prompts, credentials or exception text.
            logger.warning(
                "Gemini model=%s status=%s error_type=%s attempt=%s/%s "
                "server_delay=%s quota_ids=%s retry=%s wait=%s",
                kwargs.get("model"), code, type(exc).__name__, attempt,
                max_attempts, server_delay, quota_ids, retry, wait if retry else 0,
            )
            if not retry:
                if quota:
                    raise GeminiQuotaError(QUOTA_MESSAGE) from None
                raise
            time.sleep(wait)
            waited += wait
