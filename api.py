import time
import math
import logging
from typing import Optional, Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import orjson

logger = logging.getLogger("uvicorn.error")

app = FastAPI()

class Device:
    __slots__ = ("device_id", "heading", "latitude", "longitude", "accuracy")

    def __init__(self, device_id: str, heading: float, latitude: float, longitude: float, accuracy: float):
        self.device_id = device_id
        self.heading = heading
        self.latitude = latitude
        self.longitude = longitude
        self.accuracy = accuracy

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

class ConnectionManager:
    def __init__(self, cell_size=0.01):
        self.active_connections: Dict[str, WebSocket] = {}
        self.device_objs: Dict[str, Device] = {}
        self.pairings: Dict[str, str] = {}
        self.spatial_grid: Dict[int, Dict[int, set[str]]] = {}
        self.cell_size = cell_size
        self.static_pairs = {"test1": "test2", "test2": "test1"}

    async def connect(self, websocket: WebSocket, device: Device):
        await websocket.accept()
        self.active_connections[device.device_id] = websocket
        self.device_objs[device.device_id] = device

        cell = self._cell_coords(device.latitude, device.longitude)
        self.spatial_grid.setdefault(cell[0], {}).setdefault(cell[1], set()).add(device.device_id)

    def disconnect(self, device_id: str):
        ws = self.active_connections.pop(device_id, None)
        device = self.device_objs.pop(device_id, None)
        self.pairings.pop(device_id, None)
        if device:
            cell_x, cell_y = self._cell_coords(device.latitude, device.longitude)
            if cell_x in self.spatial_grid and cell_y in self.spatial_grid[cell_x]:
                self.spatial_grid[cell_x][cell_y].discard(device_id)
                if not self.spatial_grid[cell_x][cell_y]:
                    del self.spatial_grid[cell_x][cell_y]
                if not self.spatial_grid[cell_x]:
                    del self.spatial_grid[cell_x]

    async def relay_message(self, sender_id: str, data: dict):
        receiver_id = self.pairings.get(sender_id)
        receiver_ws = self.active_connections.get(receiver_id)
        if receiver_ws:
            await receiver_ws.send_text(orjson.dumps(data).decode())

    def update_device(self, device: Device):
        self.device_objs[device.device_id] = device

        # Update spatial grid if position changed
        for cell_x in list(self.spatial_grid.keys()):
            for cell_y in list(self.spatial_grid[cell_x].keys()):
                if device.device_id in self.spatial_grid[cell_x][cell_y]:
                    self.spatial_grid[cell_x][cell_y].remove(device.device_id)
                    if not self.spatial_grid[cell_x][cell_y]:
                        del self.spatial_grid[cell_x][cell_y]
                    if not self.spatial_grid[cell_x]:
                        del self.spatial_grid[cell_x]
                    break

        cell = self._cell_coords(device.latitude, device.longitude)
        self.spatial_grid.setdefault(cell[0], {}).setdefault(cell[1], set()).add(device.device_id)

    def find_pair(self, device: Device) -> Optional[str]:
        # 1. Static pair logic for test1/test2
        target_device_id = self.static_pairs.get(device.device_id)
        if target_device_id and target_device_id in self.active_connections:
            self.pairings[device.device_id] = target_device_id
            self.pairings[target_device_id] = device.device_id
            return target_device_id

        # 2. Proximity logic for all other devices
        cell_x, cell_y = self._cell_coords(device.latitude, device.longitude)
        nearby = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cx, cy = cell_x + dx, cell_y + dy
                if cx in self.spatial_grid and cy in self.spatial_grid[cx]:
                    nearby.update(self.spatial_grid[cx][cy])

        for other_id in nearby:
            if other_id == device.device_id or other_id in self.pairings:
                continue
            other = self.device_objs[other_id]
            dist = haversine(device.latitude, device.longitude, other.latitude, other.longitude)
            heading_diff = abs(device.heading - other.heading)
            if dist <= 50 and heading_diff < 45:
                self.pairings[device.device_id] = other_id
                self.pairings[other_id] = device.device_id
                return other_id
        return None

    def _cell_coords(self, lat: float, lon: float):
        return (int(lat / self.cell_size), int(lon / self.cell_size))

manager = ConnectionManager()

@app.websocket("/ws/device")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        initial_data = await ws.receive_json()
        device = Device(
            device_id=initial_data["device_id"],
            heading=initial_data["heading"],
            latitude=initial_data["latitude"],
            longitude=initial_data["longitude"],
            accuracy=initial_data["accuracy"]
        )
        await manager.connect(ws, device)
        await ws.send_text("200 OK: Connection Established")

        paired_device_id = manager.find_pair(device)

        while True:
            start = time.perf_counter()
            data = await ws.receive_json()
            # If not paired, try to pair again (for dynamic pairing)
            if not paired_device_id:
                manager.update_device(device)
                paired_device_id = manager.find_pair(device)
                elapsed = int((time.perf_counter() - start) * 1000)
                if not paired_device_id:
                    await ws.send_text(orjson.dumps({
                        "status": "searching",
                        "api_time": elapsed
                    }).decode())
                    continue

            # If paired, relay messages (optionally could include timing here as well)
            await manager.relay_message(device.device_id, data)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
        manager.disconnect(device.device_id)

        logger.info("WebSocket disconnected.")
        manager.disconnect(device.device_id)
    except Exception as e:
        logger.exception("WebSocket error.")
        manager.disconnect(device.device_id)
