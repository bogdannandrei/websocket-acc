import logging
import random
import sys
import time
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

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
        self.active_connection: WebSocket = None  # type: ignore

    async def connect(self, websocket: WebSocket):
        logger.debug("Attempting to accept a new WebSocket connection.")
        await websocket.accept()
        self.active_connection = websocket
        logger.info("WebSocket connection established.")

        # Send a "200 OK" message to indicate successful connection
        await self.active_connection.send_text("200 OK: Connection Established")

    def disconnect(self):
        self.active_connection = None  # type: ignore
        logger.info("WebSocket connection closed.")

    async def send_color_and_time(self, process_time: float):
        colors = ["green", "yellow", "orange", "red"]
        color = random.choice(colors)
        logger.debug(f"Selected random color: {color}")
        
        # Create a JSON object with time and color
        response = {
            "time": f"{process_time:.4f}",
            "color": color
        }

        logger.debug("Sent data to the phone: {response}")

        if self.active_connection:
            await self.active_connection.send_text(json.dumps(response))
            logger.info("Sent color and time to WebSocket client.")

manager = ConnectionManager()

@app.websocket("/ws/device")
async def websocket_endpoint(websocket: WebSocket):
    logger.debug("New WebSocket connection initiated.")
    await manager.connect(websocket)
    try:
        while True:
            # Wait for new data from the WebSocket client
            data = await websocket.receive_json()
            logger.debug(f"Received raw data from client: {data}")
            start_time = time.time()  # Start timing process

            # Ensure all expected keys are in the data
            required_keys = ["heading", "latitude", "longitude", "distance"]
            if not all(key in data for key in required_keys):
                logger.error(f"Missing keys in incoming data: {data}")
                continue  # Skip this iteration if any key is missing

            # Process the device data and create the Device instance
            device_data = Device(
                heading=data["heading"],
                latitude=data["latitude"],
                longitude=data["longitude"],
                distance=data["distance"]
            )
            logger.info(f"Processed device data: {device_data.__dict__}")

            # Calculate process time
            process_time = time.time() - start_time
            
            # Send the color and time back to the client
            logger.debug(f"Process time: {process_time:.4f} seconds")
            
            await manager.send_color_and_time(process_time)
            
    
    except WebSocketDisconnect:
        logger.warning("WebSocket client disconnected.")
        manager.disconnect()
    except Exception as e:
        logger.error(f"An error occurred: {e}")