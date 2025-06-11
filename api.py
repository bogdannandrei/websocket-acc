import logging
import math
import json
import time
import os
from typing import Optional
from collections import defaultdict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# Logging setup
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("uvicorn")

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

class Device:
    def __init__(self, device_id: str, heading: float, latitude: float, longitude: float, accuracy: float):
        self.device_id = device_id
        self.heading = heading
        self.latitude = latitude
        self.longitude = longitude
        self.accuracy = accuracy
        self.moved = False

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[tuple[WebSocket, Optional[Device], Optional[Device]]] = []
        self.device_grid = defaultdict(list)  # maps (cell_lat, cell_lon) -> list of (WebSocket, Device)
        self.pairings = {}  # device_id -> paired_device_id
        self.cache_hits = 0
        self.cache_misses = 0

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append((websocket, None, None))
        logger.info(f"Connected new client. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [(ws, dev, last) for ws, dev, last in self.active_connections if ws != websocket]
        self._remove_from_grid(websocket)
        self._remove_pairings(websocket)
        logger.info(f"Disconnected a client. Remaining clients: {len(self.active_connections)}")

    def update_device_data(self, websocket: WebSocket, new_device: Device):
        for i, (ws, _, last_dev) in enumerate(self.active_connections):
            if ws == websocket:
                moved = False
                if last_dev:
                    dist = self._calculate_distance(new_device, last_dev)
                    moved = dist >= 1.0
                else:
                    moved = True
                new_device.moved = moved
                self.active_connections[i] = (ws, new_device, new_device)

                # Reindex in spatial grid
                cell = self._get_cell(new_device)
                self._remove_from_grid(ws)
                self.device_grid[cell].append((ws, new_device))
                break

    def _remove_from_grid(self, websocket: WebSocket):
        for cell in list(self.device_grid.keys()):
            self.device_grid[cell] = [entry for entry in self.device_grid[cell] if entry[0] != websocket]
            if not self.device_grid[cell]:
                del self.device_grid[cell]

    def _remove_pairings(self, websocket: WebSocket):
        for ws, device, _ in self.active_connections:
            if ws == websocket and device:
                device_id = device.device_id
                paired_id = self.pairings.pop(device_id, None)
                if paired_id:
                    self.pairings.pop(paired_id, None)
                break

    def _get_cell(self, device: Device, precision: int = 3) -> tuple:
        return (round(device.latitude, precision), round(device.longitude, precision))

    def _get_neighboring_cells(self, cell: tuple) -> list:
        lat, lon = cell
        offsets = [-0.001, 0, 0.001]  # ~100m step
        return [(round(lat + dlat, 3), round(lon + dlon, 3)) for dlat in offsets for dlon in offsets]

    def _calculate_distance(self, d1: Device, d2: Device) -> float:
        R = 6371000
        phi1 = math.radians(d1.latitude)
        phi2 = math.radians(d2.latitude)
        delta_phi = math.radians(d2.latitude - d1.latitude)
        delta_lambda = math.radians(d2.longitude - d1.longitude)
        a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def _heading_diff(self, h1: float, h2: float) -> float:
        return min(abs(h1 - h2), 360 - abs(h1 - h2))

    def _is_facing(self, front: Device, back: Device) -> bool:
        angle_to_back = math.degrees(math.atan2(
            back.longitude - front.longitude,
            back.latitude - front.latitude
        )) % 360
        heading_diff = self._heading_diff(front.heading, angle_to_back)
        return heading_diff <= 45

    def get_cached_pair(self, device: Device) -> Optional[tuple]:
        paired_id = self.pairings.get(device.device_id)
        if not paired_id:
            return None
        for _, paired_device, _ in self.active_connections:
            if paired_device and paired_device.device_id == paired_id:
                distance = self._calculate_distance(device, paired_device)
                heading_diff = self._heading_diff(device.heading, paired_device.heading)
                if distance <= 100 and (heading_diff <= 15 or TEST_MODE):
                    self.cache_hits += 1
                    logger.debug(f"Cache HIT for device {device.device_id}")
                    return (None, paired_device, distance)
        self.pairings.pop(device.device_id, None)
        if paired_id:
            self.pairings.pop(paired_id, None)
        return None

    def find_paired_device(self, back_device: Device):
        cached = self.get_cached_pair(back_device)
        if cached:
            return cached

        self.cache_misses += 1
        logger.debug(f"Cache MISS for device {back_device.device_id}")

        back_cell = self._get_cell(back_device)
        neighboring_cells = self._get_neighboring_cells(back_cell)

        best_match = None
        min_distance = float('inf')

        for cell in neighboring_cells:
            for ws, front_device in self.device_grid.get(cell, []):
                if not front_device or front_device.device_id == back_device.device_id:
                    continue

                distance = self._calculate_distance(back_device, front_device)
                heading_diff = self._heading_diff(back_device.heading, front_device.heading)
                direction_ok = TEST_MODE or self._is_facing(front_device, back_device)

                if distance <= 100.0 and (heading_diff <= 15.0 or TEST_MODE) and direction_ok:
                    if distance < min_distance:
                        best_match = (ws, front_device, distance)
                        min_distance = distance

        if best_match:
            self.pairings[back_device.device_id] = best_match[1].device_id
            self.pairings[best_match[1].device_id] = back_device.device_id

        return best_match

manager = ConnectionManager()
app = FastAPI()

@app.websocket("/ws/device")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_text("200 OK: Connection Established")

        while True:
            start_time = time.time()
            data = await websocket.receive_json()
            logger.debug(f"Received from {websocket.client}: {data}")

            device = Device(
                device_id=data["device_id"],
                heading=data["heading"],
                latitude=data["latitude"],
                longitude=data["longitude"],
                accuracy=data["accuracy"]
            )

            manager.update_device_data(websocket, device)
            paired_info = manager.find_paired_device(device)

            api_time = int((time.time() - start_time) * 1000)

            if paired_info:
                _, paired_device, distance = paired_info
                response = {
                    "status": "paired",
                    "pairing_data": {
                        "device_id": paired_device.device_id,
                        "distance": round(distance, 2)
                    },
                    "api_time": api_time
                }
            else:
                response = {
                    "status": "searching",
                    "api_time": api_time
                }

            await websocket.send_text(json.dumps(response))

    except WebSocketDisconnect:
        logger.info(f"Disconnected: {websocket.client}")
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Error: {e}")
        manager.disconnect(websocket)

@app.get("/debug/stats")
def debug_stats():
    return {
        "cache_hits": manager.cache_hits,
        "cache_misses": manager.cache_misses,
        "active_connections": len(manager.active_connections)
    }

