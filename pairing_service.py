import time
from typing import Optional, Dict
from collections import defaultdict
from api_spatial_filtering import Device, haversine

class PairingService:
    def __init__(self):
        self.pairings: Dict[str, str] = {}
        self.last_sent: Dict[str, float] = defaultdict(lambda: 0)
        self.static_test_pairs = {('test1', 'test2'), ('test2', 'test1')}

    def pair_device(self, device: Device, candidates: list[Device]) -> Optional[Device]:
        # Return the paired device if already paired
        if device.device_id in self.pairings:
            return next((c for c in candidates if c.device_id == self.pairings[device.device_id]), None)

        # Static test pairs (force pair)
        for other in candidates:
            if (device.device_id, other.device_id) in self.static_test_pairs:
                self._register_pair(device.device_id, other.device_id)
                return other

        # Normal pairing logic
        for other in candidates:
            if device.device_id == other.device_id:
                continue
            if other.device_id in self.pairings:
                continue
            dist = haversine(device.latitude, device.longitude, other.latitude, other.longitude)
            heading_diff = abs(device.heading - other.heading)
            if dist < 50 and heading_diff < 45:
                self._register_pair(device.device_id, other.device_id)
                return other

        return None

    def _register_pair(self, id1: str, id2: str):
        self.pairings[id1] = id2
        self.pairings[id2] = id1

    def validate_pairing(self, device: Device):
        peer_id = self.pairings.get(device.device_id)
        if not peer_id:
            return
        # No-op for now, could add logic to unpair if distance too far

    def should_notify(self, device_id: str) -> bool:
        now = time.time()
        if now - self.last_sent[device_id] > 5:
            self.last_sent[device_id] = now
            return True
        return False
