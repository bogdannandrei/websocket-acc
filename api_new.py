from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict, Optional, Tuple
from math import radians, cos, sin, sqrt, atan2

app = FastAPI()

# Device class with proper types and initializations
class Device:
    def __init__(
        self,
        device_id: str,
        heading: float,
        latitude: float,
        longitude: float,
        accuracy: float,
    ):
        self.device_id: str = device_id
        self.heading: float = heading
        self.latitude: float = latitude
        self.longitude: float = longitude
        self.accuracy: float = accuracy

        # Optional values initialized as None
        self.last_distance: Optional[float] = None
        self.moved: bool = False


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.devices: Dict[str, Device] = {}

    async def connect(self, device_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[device_id] = websocket

    def disconnect(self, device_id: str):
        self.active_connections.pop(device_id, None)
        self.devices.pop(device_id, None)

    def update_device(
        self,
        device_id: str,
        heading: float,
        latitude: float,
        longitude: float,
        accuracy: float,
    ):
        if device_id in self.devices:
            device = self.devices[device_id]
            device.heading = heading
            device.latitude = latitude
            device.longitude = longitude
            device.accuracy = accuracy
        else:
            self.devices[device_id] = Device(
                device_id, heading, latitude, longitude, accuracy
            )

    def haversine_distance(
        self, lat1: float, lon1: float, lat2: float, lon2: float
    ) -> float:
        R = 6371.0  # Earth radius in km

        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = (
            sin(dlat / 2) ** 2
            + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        )
        c = 2 * atan2(sqrt(a), sqrt(1 - a))

        distance = R * c  # in kilometers
        return distance * 1000  # return in meters

    def check_pairing(self) -> Optional[Tuple[str, str, float]]:
        # For now, only pairs two devices if they exist
        if len(self.devices) < 2:
            return None
        # Pick any two devices (e.g. first two)
        device_ids = list(self.devices.keys())
        d1 = self.devices[device_ids[0]]
        d2 = self.devices[device_ids[1]]

        dist = self.haversine_distance(
            d1.latitude, d1.longitude, d2.latitude, d2.longitude
        )

        # Example threshold for pairing (e.g. less than 10m)
        if dist < 10.0:
            return (d1.device_id, d2.device_id, dist)
        return None


manager = ConnectionManager()


@app.websocket("/ws/{device_id}")
async def websocket_endpoint(websocket: WebSocket, device_id: str):
    await manager.connect(device_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            # Expecting JSON like:
            # {"heading": float, "latitude": float, "longitude": float, "accuracy": float}
            heading = float(data.get("heading", 0))
            latitude = float(data.get("latitude", 0))
            longitude = float(data.get("longitude", 0))
            accuracy = float(data.get("accuracy", 0))

            manager.update_device(device_id, heading, latitude, longitude, accuracy)

            pairing = manager.check_pairing()
            if pairing:
                d1_id, d2_id, dist = pairing
                message = {
                    "status": "paired",
                    "devices": [d1_id, d2_id],
                    "distance_m": dist,
                }
            else:
                message = {"status": "waiting"}

            await websocket.send_json(message)

    except WebSocketDisconnect:
        manager.disconnect(device_id)
