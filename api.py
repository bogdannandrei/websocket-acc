import logging
import random
import sys
import time
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import math
from typing import Optional
import os

# Set up logging using Uvicorn for colored logs
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("uvicorn")

TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

# Define a class to represent the device's data
class Device:
    def __init__(self, heading: float, latitude: float, longitude: float, accuracy: float):
        self.heading = heading
        self.latitude = latitude
        self.longitude = longitude
        self.accuracy = accuracy
        self.moved = False  # flag pentru miscare

class ConnectionManager:
    def __init__(self):
        # active_connections: list of tuples (WebSocket, Device, last_position)
        self.active_connections: list[tuple[WebSocket, Optional[Device], Optional[Device]]] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append((websocket, None, None))  # last_position None initial
        logger.info(f"Connected new client. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections = [
            (ws, dev, last_dev) for (ws, dev, last_dev) in self.active_connections if ws != websocket
        ]
        logger.info(f"Disconnected a client. Remaining clients: {len(self.active_connections)}")

    def update_device_data(self, websocket: WebSocket, new_device: Device):
        for idx, (ws, old_device, last_device) in enumerate(self.active_connections):
            if ws == websocket:
                # Verifică dacă device s-a mișcat față de ultima poziție
                moved = False
                if last_device is not None:
                    dist_moved = self._calculate_distance(new_device, last_device)
                    moved = dist_moved >= 1.0  # prag 1 metru
                    logger.debug(f"Device {ws.client} moved {dist_moved:.2f}m since last update")
                else:
                    moved = True  # prima dată considerăm că s-a mișcat

                new_device.moved = True
                self.active_connections[idx] = (websocket, new_device, new_device)
                logger.debug(f"Updated device data for websocket {ws}: {new_device.__dict__}")
                break

    def find_nearby_clients(self, device: Device, distance_threshold=100.0, heading_threshold=15.0):
        nearby_clients = []
        for ws, other, _ in self.active_connections:
            if other is None or other == device:
                continue
            distance = self._calculate_distance(device, other)
            heading_diff = abs(device.heading - other.heading)
            heading_diff = min(heading_diff, 360 - heading_diff)  # normalize

            logger.debug(f"Comparing device at {device.latitude},{device.longitude} "
                         f"to {other.latitude},{other.longitude} | Distance: {distance:.2f}m | "
                         f"Heading diff: {heading_diff:.2f}°")

            if distance <= distance_threshold and heading_diff <= heading_threshold:
                # Verifică dacă ambele device-uri s-au mișcat recent
                if TEST_MODE or (device.moved and other.moved):
                    logger.info(f"Device at {device.latitude},{device.longitude} paired with device at "
                                f"{other.latitude},{other.longitude} | Distance: {distance:.2f}m | "
                                f"Heading diff: {heading_diff:.2f}°")
                    nearby_clients.append(ws)
                else:
                    logger.info(f"Pairing ignored due to no movement: Device1 moved={device.moved}, Device2 moved={other.moved}")

        logger.info(f"Found {len(nearby_clients)} nearby clients for device at "
                    f"{device.latitude},{device.longitude}")
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
app = FastAPI()

@app.websocket("/ws/device")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial connection established message
        initial_msg = "200 OK: Connection Established"
        logger.info(f"Sending initial connection message to client {websocket.client}")
        await websocket.send_text(initial_msg)

        while True:
            data = await websocket.receive_json()
            logger.debug(f"Received data from client {websocket.client}: {data}")

            device_data = Device(
                heading=data["heading"],
                latitude=data["latitude"],
                longitude=data["longitude"],
                accuracy=data["accuracy"]
            )

            manager.update_device_data(websocket, device_data)

            # Caută clienți apropiați
            nearby_clients = manager.find_nearby_clients(device_data)

            response = {
                "status": "nearby_found" if nearby_clients else "no_nearby",
                "nearby_count": len(nearby_clients)
            }
            logger.debug(f"Sending response to client {websocket.client}: {response}")
            await websocket.send_text(json.dumps(response))

            # Optional: trimite notificare și către ceilalți din grup
            for client_ws in nearby_clients:
                try:
                    notify_msg = {
                        "status": "paired_with",
                        "lat": device_data.latitude,
                        "lon": device_data.longitude
                    }
                    logger.debug(f"Notifying nearby client {client_ws.client} with {notify_msg}")
                    await client_ws.send_text(json.dumps(notify_msg))
                except Exception as e:
                    logger.warning(f"Failed to notify nearby client: {e}")
                    manager.disconnect(client_ws)

    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {websocket.client}")
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        manager.disconnect(websocket)
