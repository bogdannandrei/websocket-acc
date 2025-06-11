import time
import math
import logging
from typing import Optional, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import orjson
import numpy as np

logger = logging.getLogger("uvicorn.error")

app = FastAPI()

# Device class with proper optional typing for last_distance and last_heading
class Device:
    __slots__ = ("device_id", "heading", "latitude", "longitude", "accuracy", "last_distance", "last_heading")

    def __init__(self, device_id: str, heading: float, latitude: float, longitude: float, accuracy: float):
        self.device_id = device_id
        self.heading = heading
        self.latitude = latitude
        self.longitude = longitude
        self.accuracy = accuracy
        self.last_distance: Optional[float] = None
        self.last_heading: Optional[float] = None

# Efficient haversine function (precalculated constants)
def haversine(lat1, lon1, lat2, lon2):
    R = 6371e3  # radius in meters
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)

    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlambda/2)**2
    c = 2*np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return R * c
# Connection manager to handle devices and pairings
class ConnectionManager:
    def __init__(self, cell_size=0.01):
        self.active_connections: Dict[WebSocket, Optional[Device]] = {}
        self.spatial_grid: Dict[int, Dict[int, set[WebSocket]]] = {}
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

    def find_pair(self, device: Device) -> Optional[tuple[Device, float]]:
        cell_x, cell_y = self._cell_coords(device.latitude, device.longitude)
        nearby_devices = []

        # Check neighbors including current cell for candidates
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cx, cy = cell_x + dx, cell_y + dy
                if cx in self.spatial_grid and cy in self.spatial_grid[cx]:
                    nearby_devices.extend(self.spatial_grid[cx][cy])

        closest_device = None
        min_dist = float('inf')

        for ws in nearby_devices:
            if ws not in self.active_connections:
                continue
            other = self.active_connections[ws]
            if not other or other.device_id == device.device_id:
                continue

            dist = haversine(device.latitude, device.longitude, other.latitude, other.longitude)
            if dist < min_dist and dist <= 50:  # threshold in meters
                min_dist = dist
                closest_device = other

        if closest_device:
            device.last_distance = min_dist
            device.last_heading = closest_device.heading
            return closest_device, min_dist
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
                paired, dist = result
                resp = {
                    "status": "paired",
                    "pairing_data": {
                        "device_id": paired.device_id,
                        "distance": round(dist, 2)
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