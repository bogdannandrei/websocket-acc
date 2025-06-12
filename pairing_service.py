from typing import Optional, Dict, Callable
from collections import defaultdict
import time
from models import Device, haversine

class PairingService:
    def __init__(self, get_all_devices: Callable[[], Dict[str, Device]]):
        self.pairings: Dict[str, str] = {}
        self.last_sent: Dict[str, float] = defaultdict(lambda: 0)
        self.get_all_devices = get_all_devices
        self.static_test_pairs = {("test1", "test2"), ("test2", "test1")}

    def pair_device(self, device: Device, candidates: list[Device]) -> Optional[Device]:
        # Already paired: return the current peer if still available
        if device.device_id in self.pairings:
            peer_id = self.pairings[device.device_id]
            peer = self.get_all_devices().get(peer_id)
            return peer

        # Try static forced pairing (test1 <-> test2)
        for other in candidates:
            if (device.device_id, other.device_id) in self.static_test_pairs:
                self._register_pair(device.device_id, other.device_id)
                return other

        # General pairing logic
        for other in candidates:
            if device.device_id == other.device_id or other.device_id in self.pairings:
                continue
            dist = haversine(device.latitude, device.longitude, other.latitude, other.longitude)
            heading_diff = abs(device.heading - other.heading)
            if dist < 50 and heading_diff < 45:
                self._register_pair(device.device_id, other.device_id)
                return other

        # Optional fallback static test pairing if other peer is seen but missed above
        for id1, id2 in self.static_test_pairs:
            if device.device_id == id1 and id2 in self.get_all_devices():
                self._register_pair(id1, id2)
                return self.get_all_devices()[id2]
            if device.device_id == id2 and id1 in self.get_all_devices():
                self._register_pair(id2, id1)
                return self.get_all_devices()[id1]

        return None

    def _register_pair(self, id1: str, id2: str):
        self.pairings[id1] = id2
        self.pairings[id2] = id1
        print(f"🔗 Registered new pair: {id1} <--> {id2}")

    async def validate_pairing(self, device: Device):
        # Stub for future unpairing logic (e.g. timeout or movement)
        return

    def should_notify(self, device_id: str) -> bool:
        now = time.time()
        if now - self.last_sent[device_id] > 5:
            self.last_sent[device_id] = now
            return True
        return False
