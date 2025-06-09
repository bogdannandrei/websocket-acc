import asyncio
import websockets
import json

async def test_ws():
    uri = "wss://websocket-acc-production.up.railway.app/ws/device"
    async with websockets.connect(uri) as websocket:
        # Trimite un mesaj de test
        data = json.dumps({
            "heading": 90.0,
            "latitude": 45.76,
            "longitude": 21.23,
            "distance": 10.5
        })
        await websocket.send(data)
        
        # Primește răspunsul
        response = await websocket.recv()
        print("📩 Răspuns primit:", response)

asyncio.run(test_ws())
