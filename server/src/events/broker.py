import json
from typing import Any

import redis.asyncio as redis

from core.config import (
    REDIS_URL, EVENT_STREAM_KEY, EVENT_STREAM_GROUP,
    EVENT_CONSUMER_NAME, EVENT_STREAM_MAXLEN, EVENT_DLQ_KEY,
)

class RedisService:
    def __init__(
        self,
        name:str = EVENT_STREAM_KEY,
        url:str = REDIS_URL,
        group_name:str = EVENT_STREAM_GROUP,
        consumer_name:str = EVENT_CONSUMER_NAME,
        maxlen:int=EVENT_STREAM_MAXLEN,
        dlq_key:str = EVENT_DLQ_KEY
    ):
      self.name = name
      self.url = url
      self.group_name = group_name
      self.consumer_name = consumer_name
      self.maxlen = maxlen
      self.dlq_key = dlq_key
      self._client : redis.Redis | None = None

    async def connect(self) -> "redis.Redis":
        if self._client is None:
            self._client = redis.from_url(url=self.url,decode_responses=True)
            await self._client.ping()
        return self._client
    
    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def ensure_group(self) -> None:
        client = await self.connect()
        try:
            await client.xgroup_create(
                name=self.name,
                groupname=self.group_name,
                id="0",
                mkstream=True
            )
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(self,event:dict[str, Any]) -> str:
        client = await self.connect()

        return await client.xadd(
            self.name,
            {"data": json.dumps(event, ensure_ascii=False)},
            maxlen=self.maxlen,
            approximate=True
        )
    
    async def read_group(self,count:int,block_ms:int) -> list[tuple[str, dict[str, str]]]:
        client = await self.connect()

        resp = await client.xreadgroup(
            groupname=self.group_name,
            consumername=self.consumer_name,
            streams={self.name: ">"},
            count=count,
            block=block_ms
        )

        if not resp:
            return []
        _stream , entries = resp[0]
        return entries
    
    async def claim_stale(self,min_idle_ms:int,count:int,start_id:str = "0-0") -> list[tuple[str, dict[str, str]]]:
        client = await self.connect()

        _cursor,entries,_deleted = await client.xautoclaim(
            name=self.name,
            groupname=self.group_name,
            consumername=self.consumer_name,
            min_idle_time=min_idle_ms,
            start_id=start_id,
            count=count
        )

        return entries
    
    async def ack(self,entry_id:str) -> None :
        client = await self.connect()

        await client.xack(
            self.name,
            self.group_name,
            entry_id
        )

    async def dead_letter(self, entry_id: str, raw: str, reason: str) -> None:
        """Park a poison message so it stops blocking, then ack the original."""
        client = await self.connect()
        await client.xadd(self.dlq_key, {"data": raw, "reason": reason, "src_id": entry_id})
        await self.ack(entry_id)

broker = RedisService()
