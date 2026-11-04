"""Thin async client for the Instagram API with Instagram Login (graph.instagram.com)."""

from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings

PROFILE_FIELDS = "name,username,profile_pic,is_user_follow_business,is_business_follow_user"
ME_FIELDS = "user_id,username,name,profile_picture_url,account_type"
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp"
)
WEBHOOK_FIELDS = "comments,messages,messaging_postbacks"
OAUTH_SCOPES = (
    "instagram_business_basic,"
    "instagram_business_manage_messages,"
    "instagram_business_manage_comments"
)

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class InstagramAPIError(Exception):
    def __init__(self, status: int, data: dict[str, Any] | None, text: str = ""):
        error = (data or {}).get("error") or {}
        self.status = status
        self.code: int | None = error.get("code")
        self.subcode: int | None = error.get("error_subcode")
        self.message: str = error.get("message") or text or f"HTTP {status}"
        super().__init__(f"[{self.code}/{self.subcode}] {self.message}")

    @property
    def is_token_error(self) -> bool:
        return self.code == 190

    @property
    def is_rate_limited(self) -> bool:
        return self.code in (4, 17, 32, 613) or self.status == 429

    @property
    def is_outside_window(self) -> bool:
        """The user hasn't messaged in 24h, or a private reply was already used."""
        return self.subcode in (2534022, 2018278) or (
            self.code == 10 and "window" in self.message.lower()
        )

    @property
    def is_retryable(self) -> bool:
        return self.is_rate_limited or self.status >= 500 or self.code in (1, 2)


def _raise_for(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.is_error or (isinstance(data, dict) and "error" in data):
        raise InstagramAPIError(response.status_code, data, response.text[:500])
    return data if isinstance(data, dict) else {}


class InstagramClient:
    def __init__(self, access_token: str, ig_user_id: str, http: httpx.AsyncClient | None = None):
        settings = get_settings()
        self.base = f"{settings.graph_base_url}/{settings.graph_api_version}"
        self.token = access_token
        self.ig_user_id = ig_user_id
        self._http = http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        url = f"{self.base}/{path.lstrip('/')}"
        if self._http is not None:
            response = await self._http.request(
                method, url, params=params, json=json, headers=headers
            )
        else:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
                response = await http.request(
                    method, url, params=params, json=json, headers=headers
                )
        return _raise_for(response)

    # -- messaging -------------------------------------------------------

    async def send_message(self, recipient: dict[str, str], message: dict[str, Any]) -> dict:
        """recipient is {"id": IGSID} or {"comment_id": ...} for a private reply."""
        return await self._request(
            "POST",
            f"{self.ig_user_id}/messages",
            json={"recipient": recipient, "message": message},
        )

    async def reply_to_comment(self, comment_id: str, text: str) -> dict:
        return await self._request("POST", f"{comment_id}/replies", json={"message": text})

    async def get_user_profile(self, igsid: str) -> dict:
        return await self._request("GET", igsid, params={"fields": PROFILE_FIELDS})

    # -- account ---------------------------------------------------------

    async def get_me(self) -> dict:
        return await self._request("GET", "me", params={"fields": ME_FIELDS})

    async def list_media(self, limit: int = 24, after: str | None = None) -> dict:
        params: dict[str, Any] = {"fields": MEDIA_FIELDS, "limit": limit}
        if after:
            params["after"] = after
        return await self._request("GET", f"{self.ig_user_id}/media", params=params)

    async def get_media(self, media_id: str) -> dict:
        return await self._request("GET", media_id, params={"fields": MEDIA_FIELDS})

    async def subscribe_webhooks(self, fields: str = WEBHOOK_FIELDS) -> dict:
        return await self._request(
            "POST", f"{self.ig_user_id}/subscribed_apps", params={"subscribed_fields": fields}
        )

    async def get_subscriptions(self) -> dict:
        return await self._request("GET", f"{self.ig_user_id}/subscribed_apps")


# -- OAuth (Instagram Business Login) ---------------------------------------


def build_authorize_url(state: str, app_id: str) -> str:
    settings = get_settings()
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": settings.oauth_redirect_uri,
            "response_type": "code",
            "scope": OAUTH_SCOPES,
            "state": state,
            "enable_fb_login": "0",
            "force_reauth": "true",
        }
    )
    return f"https://www.instagram.com/oauth/authorize?{query}"


async def exchange_code_for_token(code: str, app_id: str, app_secret: str) -> dict:
    """Returns {"access_token": short-lived token, "user_id": ..., "permissions": ...}."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        response = await http.post(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": settings.oauth_redirect_uri,
                "code": code.removesuffix("#_"),
            },
        )
    data = _raise_for(response)
    # Newer API versions wrap the result in {"data": [...]}
    if isinstance(data.get("data"), list) and data["data"]:
        data = data["data"][0]
    return data


async def exchange_for_long_lived_token(short_token: str, app_secret: str) -> dict:
    """Returns {"access_token", "token_type", "expires_in"} (~60 days)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        response = await http.get(
            f"{settings.graph_base_url}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": app_secret,
                "access_token": short_token,
            },
        )
    return _raise_for(response)


async def refresh_long_lived_token(token: str) -> dict:
    """Extends a long-lived token (must be >24h old and not expired) by another ~60 days."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
        response = await http.get(
            f"{settings.graph_base_url}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token},
        )
    return _raise_for(response)
