"""Sample async patterns."""

from __future__ import annotations


class Client:
    """Fake async HTTP client."""

    async def get(self, url: str) -> str:
        """Fetch a payload."""
        return f"payload:{url}"


async def fetch(client: Client) -> str:
    """Module-level coroutine awaited by callers."""
    return await client.get("fetch")


class Fetcher:
    """Await a client stored on self."""

    def __init__(self, client: Client) -> None:
        self.client = client

    async def fetch(self, url: str) -> str:
        """Await the client call."""
        return await self.client.get(url)


async def download(client: Client) -> str:
    """Standalone coroutine."""
    return await client.get("download")
