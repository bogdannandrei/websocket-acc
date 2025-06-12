import time
import math
import logging
import asyncio
from typing import Optional, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import orjson

from pairing_service import PairingService
from relay_service import RelayService
from models import Device, haversine

logger = logging.getLogger("uvicorn.error")

app = FastAPI()

class ConnectionManager:
    def __init__(self, cell_size=0.01):
        self.active_connections: Dict[WebSocket, Optional[Device]] = {}
        self.device_to_ws: Dict[str, WebSocket] = {}
        self.spatial_grid: Dict[int, Dict[int, set[WebSocket]]] = {}
        self.cell_size = cell_size

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = None

    def disconnect(self, websocket: WebSocket):
        device = self.active_connections.pop(websocket, None)
        if device:
            self.device_to_ws.pop(device.device_id, None)
            cell_x, cell_y = self._cell_coords(device.latitude, device.longitude)
            if cell_x in self.spatial_grid and cell_y in self.spatial_grid[cell_x]:
                self.spatial_grid[cell_x][cell_y].discard(websocket)
                if not self.spatial_grid[cell_x][cell_y]:
                    del self.spatial_grid[cell_x][cell_y]
                if not self.spatial_grid[cell_x]:
                    del self.spatial_grid[cell_x]

    def _cell_coords(self, lat: float, lon: float):
        return (int(lat / self.cell_size), int(lon / self.cell_size))

    def update_device(self, websocket: WebSocket, device: Device):
        old_device = self.active_connections.get(websocket)
        if old_device:
            old_cell = self._cell_coords(old_device.latitude, old_device.longitude)
            new_cell = self._cell_coords(device.latitude, device.longitude)
            if old_cell != new_cell:
                if old_cell[0] in self.spatial_grid and old_cell[1] in self.spatial_grid[old_cell[0]]:
                    self.spatial_grid[old_cell[0]][old_cell[1]].discard(websocket)
                    if not self.spatial_grid[old_cell[0]][old_cell[1]]:
                        del self.spatial_grid[old_cell[0]][old_cell[1]]
                    if not self.spatial_grid[old_cell[0]]:
                        del self.spatial_grid[old_cell[0]]
                self.spatial_grid.setdefault(new_cell[0], {}).setdefault(new_cell[1], set()).add(websocket)
        else:
            cell = self._cell_coords(device.latitude, device.longitude)
            self.spatial_grid.setdefault(cell[0], {}).setdefault(cell[1], set()).add(websocket)

        self.active_connections[websocket] = device
        self.device_to_ws[device.device_id] = websocket

    def get_nearby_devices(self, device: Device):
        cell_x, cell_y = self._cell_coords(device.latitude, device.longitude)
        nearby_devices = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cx, cy = cell_x + dx, cell_y + dy
                if cx in self.spatial_grid and cy in self.spatial_grid[cx]:
                    nearby_devices.extend(self.spatial_grid[cx][cy])
        return [self.active_connections[ws] for ws in nearby_devices if ws in self.active_connections]

    def get_websocket_by_device_id(self, device_id: str) -> Optional[WebSocket]:
        return self.device_to_ws.get(device_id)

manager = ConnectionManager()
pairing_service = PairingService(lambda: manager.active_connections)
relay_service = RelayService(manager.device_to_ws)

@app.websocket("/ws/device")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    await ws.send_text("200 OK: Connection Established")
    try:
        while True:
            start = time.time()
            data = await ws.receive_json()

            dev = Device(
                device_id=data["device_id"],
                heading=data["heading"],
                latitude=data["latitude"],
                longitude=data["longitude"],
                accuracy=data["accuracy"]
            )

            manager.update_device(ws, dev)
            candidates = manager.get_nearby_devices(dev)
            result = pairing_service.pair_device(dev, candidates)
            asyncio.create_task(pairing_service.validate_pairing(dev))

            elapsed = int((time.time() - start) * 1000)

            if result:
                notify = pairing_service.should_notify(dev.device_id)
                response = {
                    "status": "paired" if notify else "peer_update",
                    "pairing_data": {
                        "device_id": result.device_id,
                        "latitude": result.latitude,
                        "longitude": result.longitude,
                        "heading": result.heading
                    },
                    "api_time": elapsed,
                    "sent_time": int(time.time() * 1000)
                }
                await ws.send_text(orjson.dumps(response).decode())
                await relay_service.forward(dev, result.device_id)
            else:
                await ws.send_text(orjson.dumps({"status": "searching", "api_time": elapsed}).decode())

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
        manager.disconnect(ws)
    except Exception as e:
        logger.exception("WebSocket error.")
        manager.disconnect(ws)
