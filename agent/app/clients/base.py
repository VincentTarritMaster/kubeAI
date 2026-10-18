import httpx


class UpstreamError(Exception):
    def __init__(self, source: str, message: str):
        self.source = source
        self.message = message
        super().__init__(message)


async def get_json(client: httpx.AsyncClient, source: str, path: str, **kwargs):
    try:
        response = await client.get(path, **kwargs)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("A JSON object is expected")
        return data
    except httpx.HTTPStatusError as error:
        raise UpstreamError(source, f"{source} returned HTTP {error.response.status_code}") from error
    except httpx.TimeoutException as error:
        raise UpstreamError(source, f"Request timed out for {source}") from error
    except httpx.RequestError as error:
        raise UpstreamError(source, f"Cannot reach {source}") from error
    except ValueError as error:
        raise UpstreamError(source, f"Invalid JSON response from {source}") from error
