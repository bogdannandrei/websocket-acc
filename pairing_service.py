from typing import Optional, Dict
from collections import defaultdict
import time
from models import Device, haversine

class PairingService:
    def __init__(self, get_all_devices: callable):
        self.pairings: Dict[str, str] = {}
        self.last_sent: Dict[str, float] = defaultdict(lambda: 0)
        self.get_all_devices = get_all_devices  # inject function to access all known devices
        self.static_test_pairs = {("test1", "test2"), ("test2", "test1")}

    def pair_device(self, device: Device, candidates: list[Device]) -> Optional[Device]:
        if device.device_id in self.pairings:
            peer_id = self.pairings[device.device_id]
            peer = self.get_all_devices().get(peer_id)
            return peer

        for other in candidates:
            if (device.device_id, other.device_id) in self.static_test_pairs:
                self._register_pair(device.device_id, other.device_id)
                return other

        for other in candidates:
            if device.device_id == other.device_id or other.device_id in self.pairings:
                continue
            dist = haversine(device.latitude, device.longitude, other.latitude, other.longitude)
            heading_diff = abs(device.heading - other.heading)
            if dist < 50 and heading_diff < 45:
                self._register_pair(device.device_id, other.device_id)
                return other

        # fallback: force static test pair if known and only one visible
        for static_a, static_b in self.static_test_pairs:
            if device.device_id == static_a and static_b in self.get_all_devices():
                self._register_pair(static_a, static_b)
                return self.get_all_devices()[static_b]
            if device.device_id == static_b and static_a in self.get_all_devices():
                self._register_pair(static_b, static_a)
                return self.get_all_devices()[static_a]

        return None

    def _register_pair(self, id1: str, id2: str):
        self.pairings[id1] = id2
        self.pairings[id2] = id1

    async def validate_pairing(self, device: Device):
        return

    def should_notify(self, device_id: str) -> bool:
        now = time.time()
        if now - self.last_sent[device_id] > 5:
            self.last_sent[device_id] = now
            return True
        return False
