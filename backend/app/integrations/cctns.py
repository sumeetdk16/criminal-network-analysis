"""
CCTNS adapter - Crime and Criminal Tracking Network and Systems.

CCTNS is the national FIR and crime-record backbone. What this system needs
from it is the FIR text and the accused particulars; what it must never do is
write to it.

Configure with CCTNS_URL and CCTNS_KEY. Without them the adapter runs in mock
mode against a local fixture so the integration path is demonstrable on a
laptop, and says plainly that it is doing so.
"""

from __future__ import annotations

import json
import os
import urllib.request

from .base import SourceAdapter

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "cctns_sample.json")


class CctnsAdapter(SourceAdapter):
    key = "cctns"
    system = "CCTNS (Crime and Criminal Tracking Network and Systems)"
    record_type = "fir"

    FIELD_MAP = {
        "firNumber": "fir_id",
        "policeStation": "station",
        "actsAndSections": "sections",
        "dateOfRegistration": "registered_on",
        "district": "district",
        "briefFacts": "narrative",
        "language": "language",
        "accusedPersons": "accused",
    }

    def _fetch_live(self, **q):
        url = f"{self.base_url.rstrip('/')}/api/v1/fir?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode()).get("records", [])

    def _fetch_mock(self, **q):
        if not os.path.exists(FIXTURE):
            return []
        with open(FIXTURE, encoding="utf-8") as f:
            recs = json.load(f)
        ps = q.get("district")
        return [r for r in recs if not ps or r.get("district") == ps]
