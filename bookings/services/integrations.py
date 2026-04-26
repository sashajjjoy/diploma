from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from django.utils import timezone


def build_integration_headers(integration):
    headers = {"User-Agent": "restaurant-booking/1.0"}
    if integration.auth_type == integration.AUTH_BEARER and integration.secret_token:
        headers["Authorization"] = f"Bearer {integration.secret_token}"
    elif integration.auth_type == integration.AUTH_API_KEY and integration.secret_token:
        headers["X-API-Key"] = integration.secret_token
    return headers


def check_external_integration(integration):
    request = Request(integration.base_url, headers=build_integration_headers(integration), method="GET")
    try:
        with urlopen(request, timeout=integration.timeout_seconds) as response:
            status_code = getattr(response, "status", 200)
            success = 200 <= status_code < 400
            note = f"HTTP {status_code}"
    except HTTPError as exc:
        success = False
        note = f"HTTP {exc.code}: {exc.reason}"
    except URLError as exc:
        success = False
        note = f"Connection error: {exc.reason}"
    except Exception as exc:
        success = False
        note = f"Unexpected error: {exc}"

    integration.last_check_success = success
    integration.last_checked_at = timezone.now()
    integration.last_check_note = note
    integration.save(update_fields=["last_check_success", "last_checked_at", "last_check_note", "updated_at"])
    return success, note
