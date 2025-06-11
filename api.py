import logging
import math
import orjson
import time
import os
from typing import Optional, Tuple, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("uvicorn")

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

class Device:
    def __init__(self, device_id: str, heading: float, latitude: float, longitude: float, accuracy: float):
        self.device_id = device_id
        self.heading = heading
        self.latitude = latitude
        self.longitude = longitude
        self.accuracy = accuracy
        self.moved = True

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[Tuple[WebSocket, Optional[Device]]] = []
        self.device_grid = defaultdict(list)  # (lat_cell, lon_cell) -> List[(WebSocket, Device)]
        self.pairings = {}
        self.cache_hits = 0
        self.cache_misses = 0

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append((ws, None))
        logger.info(f"Client connected. Total: {len(self.active_connections)}")

    def disconnect(self, ws: WebSocket):
        self.active_connections = [(w, d) for w, d in self.active_connections if w != ws]
        for cell in list(self.device_grid):
            self.device_grid[cell] = [(w, d) for w, d in self.device_grid[cell] if w != ws]
            if not self.device_grid[cell]:
                del self.device_grid[cell]
        self.pairings = {k: v for k, v in self.pairings.items() if k != ws}

    def update_device(self, ws: WebSocket, new_dev: Device):
        lat_cell = round(new_dev.latitude, 3)
        lon_cell = round(new_dev.longitude, 3)
        cell = (lat_cell, lon_cell)

        for i, (w, _) in enumerate(self.active_connections):
            if w == ws:
                self.active_connections[i] = (ws, new_dev)
                break

        # Remove from old cells
        for grid_cell in list(self.device_grid):
            self.device_grid[grid_cell] = [(w, d) for w, d in self.device_grid[grid_cell] if w != ws]
            if not self.device_grid[grid_cell]:
                del self.device_grid[grid_cell]

        self.device_grid[cell].append((ws, new_dev))

    def _distance(self, d1: Device, d2: Device) -> float:
        R = 6371000
        phi1 = math.radians(d1.latitude)
        phi2 = math.radians(d2.latitude)
        d_phi = math.radians(d2.latitude - d1.latitude)
        d_lambda = math.radians(d2.longitude - d1.longitude)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _heading_diff(self, h1: float, h2: float) -> float:
        return min(abs(h1 - h2), 360 - abs(h1 - h2))

    def _is_facing(self, front: Device, back: Device) -> bool:
        angle = math.degrees(math.atan2(back.longitude - front.longitude, back.latitude - front.latitude)) % 360
        return self._heading_diff(front.heading, angle) <= 45

    def _neighbor_cells(self, cell: Tuple[float, float]) -> List[Tuple[float, float]]:
        lat, lon = cell
        offsets = [-0.001, 0.0, 0.001]
        return [(round(lat + dlat, 3), round(lon + dlon, 3)) for dlat in offsets for dlon in offsets]

    def _get_cached_pair(self, device: Device) -> Optional[Tuple[Device, float]]:
        pid = self.pairings.get(device.device_id)
        if pid:
            for _, other in self.active_connections:
                if other and other.device_id == pid:
                    distance = self._distance(device, other)
                    if distance <= 100 and (self._heading_diff(device.heading, other.heading) <= 15 or TEST_MODE):
                        self.cache_hits += 1
                        return other, distance
                    else:
                        break
        self.cache_misses += 1
        self.pairings.pop(device.device_id, None)
        return None

    def find_pair(self, device: Device) -> Optional[Tuple[Device, float]]:
        cached = self._get_cached_pair(device)
        if cached:
            return cached

        cell = (round(device.latitude, 3), round(device.longitude, 3))
        neighbors = self._neighbor_cells(cell)

        best_match = None
        min_dist = 100.01

        for neighbor in neighbors:
            for _, other in self.device_grid.get(neighbor, []):
                if other.device_id == device.device_id:
                    continue

                dist = self._distance(device, other)
                if dist > 100.0 or (self._heading_diff(device.heading, other.heading) > 15 and not TEST_MODE):
                    continue
                if not TEST_MODE and not self._is_facing(other, device):
                    continue

                if dist < min_dist:
                    best_match = other
                    min_dist = dist

        if best_match:
            self.pairings[device.device_id] = best_match.device_id
            self.pairings[best_match.device_id] = device.device_id
            return best_match, min_dist

        return None

manager = ConnectionManager()
app = FastAPI()

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

@app.get("/debug/stats")
def get_stats():
    return {
        "cache_hits": manager.cache_hits,
        "cache_misses": manager.cache_misses,
        "active_connections": len(manager.active_connections)
    }
