import logging
import random
import sys
import time
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import math
from typing import Optional

# Set up logging using Uvicorn for colored logs
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("uvicorn")

# Define a class to represent the device's data
class Device:
    def __init__(self, heading: float, latitude: float, longitude: float, distance: float):
        self.heading = heading
        self.latitude = latitude
        self.longitude = longitude
        self.distance = distance

# FastAPI app instance
app = FastAPI()

# Manager to handle WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[tuple[WebSocket, Optional[Device]]] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append((websocket, None))
        logger.info(f"Connected new client. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [
            (ws, dev) for (ws, dev) in self.active_connections if ws != websocket
        ]
        logger.info(f"Disconnected a client. Remaining clients: {len(self.active_connections)}")

    def update_device_data(self, websocket: WebSocket, device: Device):
        for idx, (ws, _) in enumerate(self.active_connections):
            if ws == websocket:
                self.active_connections[idx] = (websocket, device)
                break

    def find_nearby_clients(self, device: Device, distance_threshold=100.0, heading_threshold=15.0):
        nearby_clients = []
        for ws, other in self.active_connections:
            if other is None or other == device:
                continue
            distance = self._calculate_distance(device, other)
            heading_diff = abs(device.heading - other.heading)
            heading_diff = min(heading_diff, 360 - heading_diff)  # normalize

            if distance <= distance_threshold and heading_diff <= heading_threshold:
                nearby_clients.append(ws)
        return nearby_clients

    def _calculate_distance(self, d1: Device, d2: Device):
        # Haversine formula for distance between two lat/lon points
        R = 6371000  # Earth radius in meters
        phi1, phi2 = math.radians(d1.latitude), math.radians(d2.latitude)
        d_phi = math.radians(d2.latitude - d1.latitude)
        d_lambda = math.radians(d2.longitude - d1.longitude)

        a = math.sin(d_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

        return R * c

manager = ConnectionManager()

@app.websocket("/ws/device")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()

            device_data = Device(
                heading=data["heading"],
                latitude=data["latitude"],
                longitude=data["longitude"],
                distance=data["distance"]
            )
            manager.update_device_data(websocket, device_data)

            # Caută clienți apropiați
            nearby_clients = manager.find_nearby_clients(device_data)

            response = {
                "status": "nearby_found" if nearby_clients else "no_nearby",
                "nearby_count": len(nearby_clients)
            }

            await websocket.send_text(json.dumps(response))

            # Optional: trimite notificare și către ceilalți din grup
            for client_ws in nearby_clients:
                try:
                    await client_ws.send_text(json.dumps({
                        "status": "paired_with",
                        "lat": device_data.latitude,
                        "lon": device_data.longitude
                    }))
                except Exception as e:
                    logger.warning(f"Failed to notify nearby client: {e}")
                    manager.disconnect(client_ws)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        manager.disconnect(websocket)
