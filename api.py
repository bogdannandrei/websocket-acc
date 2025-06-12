import time
import math
import logging
from typing import Optional, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import orjson

logger = logging.getLogger("uvicorn.error")

app = FastAPI()

# Device class with proper optional typing
class Device:
    __slots__ = ("device_id", "heading", "latitude", "longitude", "accuracy")

    def __init__(self, device_id: str, heading: float, latitude: float, longitude: float, accuracy: float):
        self.device_id = device_id
        self.heading = heading
        self.latitude = latitude
        self.longitude = longitude
        self.accuracy = accuracy

# Connection manager to handle devices and pairings
class ConnectionManager:
    def __init__(self, cell_size=0.01):
        self.active_connections: Dict[WebSocket, Optional[Device]] = {}
        self.spatial_grid: Dict[int, Dict[int, set[WebSocket]]] = {}
        self.pairings: Dict[str, str] = {}
        self.cell_size = cell_size

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = None

    def disconnect(self, websocket: WebSocket):
        device = self.active_connections.pop(websocket, None)
        if device:
            cell_x, cell_y = self._cell_coords(device.latitude, device.longitude)
            if cell_x in self.spatial_grid and cell_y in self.spatial_grid[cell_x]:
                self.spatial_grid[cell_x][cell_y].discard(websocket)
                if not self.spatial_grid[cell_x][cell_y]:
                    del self.spatial_grid[cell_x][cell_y]
                if not self.spatial_grid[cell_x]:
                    del self.spatial_grid[cell_x]
            # Remove pairing
            self.pairings.pop(device.device_id, None)

    def _cell_coords(self, lat: float, lon: float):
        return (int(lat / self.cell_size), int(lon / self.cell_size))

    def update_device(self, websocket: WebSocket, device: Device):
        old_device = self.active_connections.get(websocket)
        if old_device:
            old_cell = self._cell_coords(old_device.latitude, old_device.longitude)
            new_cell = self._cell_coords(device.latitude, device.longitude)
            if old_cell != new_cell:
                # Remove from old cell
                if old_cell[0] in self.spatial_grid and old_cell[1] in self.spatial_grid[old_cell[0]]:
                    self.spatial_grid[old_cell[0]][old_cell[1]].discard(websocket)
                    if not self.spatial_grid[old_cell[0]][old_cell[1]]:
                        del self.spatial_grid[old_cell[0]][old_cell[1]]
                    if not self.spatial_grid[old_cell[0]]:
                        del self.spatial_grid[old_cell[0]]
                # Add to new cell
                self.spatial_grid.setdefault(new_cell[0], {}).setdefault(new_cell[1], set()).add(websocket)
        else:
            # New device, add to spatial grid
            cell = self._cell_coords(device.latitude, device.longitude)
            self.spatial_grid.setdefault(cell[0], {}).setdefault(cell[1], set()).add(websocket)

        self.active_connections[websocket] = device

    def find_pair(self, device: Device) -> Optional[Device]:
        # Return already paired device if exists
        if device.device_id in self.pairings:
            paired_id = self.pairings[device.device_id]
            for ws, dev in self.active_connections.items():
                if dev and dev.device_id == paired_id:
                    return dev

        cell_x, cell_y = self._cell_coords(device.latitude, device.longitude)
        nearby_devices = []

        # Check neighbors including current cell for candidates
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cx, cy = cell_x + dx, cell_y + dy
                if cx in self.spatial_grid and cy in self.spatial_grid[cx]:
                    nearby_devices.extend(self.spatial_grid[cx][cy])

        for ws in nearby_devices:
            if ws not in self.active_connections:
                continue
            other = self.active_connections[ws]
            if not other or other.device_id == device.device_id:
                continue

            # Pair devices
            self.pairings[device.device_id] = other.device_id
            self.pairings[other.device_id] = device.device_id
            return other

        return None

manager = ConnectionManager()

@app.websocket("/ws/device")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text("200 OK: Connection Established")

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
            result = manager.find_pair(dev)
            elapsed = int((time.time() - start) * 1000)

            if result:
                resp = {
                    "status": "paired",
                    "pairing_data": {
                        "device_id": result.device_id,
                        "latitude": result.latitude,
                        "longitude": result.longitude,
                        "heading": result.heading
                    },
                    "api_time": elapsed
                }
            else:
                resp = {"status": "searching", "api_time": elapsed}

            await ws.send_text(orjson.dumps(resp).decode())

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
        manager.disconnect(ws)
    except Exception as e:
        logger.exception("WebSocket error.")
        manager.disconnect(ws)
