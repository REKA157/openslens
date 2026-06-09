"""
Client HTTP pour appeler l'API WAHA depuis le backend.
On l'utilise pour :
  - lire l'historique d'un groupe (backfill)
  - télécharger les médias (audio, image, document)
  - interroger l'état de session

Particularité : les URLs de médias dans les payloads webhook WEBJS sont
souvent du type `http://localhost:3000/api/files/...`. Le `localhost` ici
est interne au conteneur WAHA, pas joignable depuis le backend. On en
extrait toujours le path et on l'appelle via notre base_url public + clé API.
"""

from urllib.parse import urlparse

import httpx

from app.config import settings


def _strip_to_path(url: str) -> str:
    """Garde uniquement path + query d'une URL absolue (sert à neutraliser le localhost)."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return path


class WahaClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        session_name: str | None = None,
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or settings.waha_base_url).rstrip("/")
        self.api_key = api_key or settings.waha_api_key
        self.session_name = session_name or settings.waha_session_name
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key},
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_session_status(self) -> dict:
        r = await self._client.get(f"/api/sessions/{self.session_name}")
        r.raise_for_status()
        return r.json()

    async def list_groups(self) -> list[dict]:
        r = await self._client.get(f"/api/{self.session_name}/groups")
        r.raise_for_status()
        return r.json()

    async def fetch_messages(
        self,
        chat_id: str,
        limit: int = 100,
        download_media: bool = False,
    ) -> list[dict]:
        params = {"limit": limit, "downloadMedia": str(download_media).lower()}
        r = await self._client.get(
            f"/api/{self.session_name}/chats/{chat_id}/messages",
            params=params,
        )
        r.raise_for_status()
        return r.json()

    async def download_url(self, url: str) -> tuple[bytes, str]:
        """
        Télécharge un média depuis une URL fournie par le payload webhook.
        On normalise l'URL en path pur pour passer par notre base_url public
        (les URLs webhook contiennent souvent `localhost:3000` qui n'est pas
        joignable depuis l'extérieur du conteneur WAHA).
        """
        path = _strip_to_path(url)
        r = await self._client.get(path)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "application/octet-stream")
        return r.content, content_type

    async def download_media(self, message_id: str) -> tuple[bytes, str]:
        """Fallback : tente de récupérer le média par l'endpoint message id."""
        r = await self._client.get(
            f"/api/{self.session_name}/messages/{message_id}/media"
        )
        r.raise_for_status()
        content_type = r.headers.get("content-type", "application/octet-stream")
        return r.content, content_type
