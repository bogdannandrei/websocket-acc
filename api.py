import logging
import math
import json
import time
import os
from typing import Optional
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

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append((websocket, None, None))
        logger.info(f"Connected new client. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [(ws, dev, last) for ws, dev, last in self.active_connections if ws != websocket]
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
                break

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

    def find_paired_device(self, back_device: Device):
        best_match = None
        min_distance = float('inf')
        for ws, front_device, _ in self.active_connections:
            if not front_device or front_device.device_id == back_device.device_id:
                continue

            distance = self._calculate_distance(back_device, front_device)
            heading_diff = self._heading_diff(back_device.heading, front_device.heading)

            # Verifică dacă front_device e în fața back_device (în funcție de heading)
            direction_ok = TEST_MODE or self._is_facing(front_device, back_device)

            if distance <= 100.0 and (heading_diff <= 15.0 or TEST_MODE) and direction_ok:
                if distance < min_distance:
                    best_match = (ws, front_device, distance)
                    min_distance = distance

        return best_match

    def _is_facing(self, front: Device, back: Device) -> bool:
        # Între 150° și 210° față în față
        angle_to_back = math.degrees(math.atan2(
            back.longitude - front.longitude,
            back.latitude - front.latitude
        )) % 360

        heading_diff = self._heading_diff(front.heading, angle_to_back)
        return heading_diff <= 45

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
