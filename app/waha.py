"""
Client HTTP pour appeler l'API WAHA depuis le backend.
On l'utilise pour :
  - lire l'historique d'un groupe (backfill)
  - télécharger les médias (audio, image, document)
  - interroger l'état de session
"""

import httpx

from app.config import settings


class WahaClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        session_name: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or settings.waha_base_url).rstrip("/")
        self.api_key = api_key or settings.waha_api_key
        self.session_name = session_name or settings.waha_session_name
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key},
            timeout=timeout,
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
        """Récupère l'historique d'un chat (jusqu'à ~30j en NOWEB)."""
        params = {"limit": limit, "downloadMedia": str(download_media).lower()}
        r = await self._client.get(
            f"/api/{self.session_name}/chats/{chat_id}/messages",
            params=params,
        )
        r.raise_for_status()
        return r.json()

    async def download_media(self, message_id: str) -> tuple[bytes, str]:
        """
        Télécharge le binaire d'un média via WAHA.
        Retourne (content, content_type).
        """
        # WAHA expose les médias via /api/files/{file_id} ou directement
        # via une URL signée dans le payload. On essaie l'endpoint standard.
        r = await self._client.get(f"/api/{self.session_name}/messages/{message_id}/media")
        r.raise_for_status()
        content_type = r.headers.get("content-type", "application/octet-stream")
        return r.content, content_type

    async def download_url(self, url: str) -> tuple[bytes, str]:
        """Télécharge un média depuis une URL fournie dans le payload webhook."""
        # Si l'URL est un chemin relatif WAHA, on préfixe
        if url.startswith("/"):
            r = await self._client.get(url)
        else:
            r = await self._client.get(url)
        r.raise_for_status()
        return r.content, r.headers.get("content-type", "application/octet-stream")
