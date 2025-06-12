import asyncio
import orjson
from typing import Dict
from fastapi import WebSocket

class RelayService:
    def __init__(self, device_to_ws: Dict[str, WebSocket]):
        self.device_to_ws = device_to_ws

    async def forward(self, from_device, to_device_id: str):
        to_ws = self.device_to_ws.get(to_device_id)
        if to_ws:
            message = {
                "type": "peer_update",
                "peer": {
                    "device_id": from_device.device_id,
                    "latitude": from_device.latitude,
                    "longitude": from_device.longitude,
                    "heading": from_device.heading,
                },
                "sent_time": int(time.time() * 1000)
            }
            try:
                await to_ws.send_text(orjson.dumps(message).decode())
            except Exception:
                pass
