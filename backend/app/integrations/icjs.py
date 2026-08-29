"""
ICJS adapter - Interoperable Criminal Justice System.

ICJS stitches together police, prosecution, courts, prisons and forensics. For
network analysis the valuable part is case progression and custody status: a
subject who is in judicial custody cannot have been at a meeting last Tuesday,
and a system that does not know that will happily assert that they were.

Configure with ICJS_URL and ICJS_KEY; otherwise mock mode against a fixture.
"""

from __future__ import annotations

import json
import os
import urllib.request

from .base import SourceAdapter

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "icjs_sample.json")


class IcjsAdapter(SourceAdapter):
    key = "icjs"
    system = "ICJS (Interoperable Criminal Justice System)"
    record_type = "case_status"

    FIELD_MAP = {
        "personId": "external_person_id",
        "personName": "name",
        "caseNumber": "case_id",
        "courtName": "court",
        "stage": "stage",
        "custodyStatus": "custody_status",
        "custodyFrom": "custody_from",
        "custodyTo": "custody_to",
        "lastHearing": "last_hearing",
    }

    def _fetch_live(self, **q):
        url = f"{self.base_url.rstrip('/')}/api/v1/case-status?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode()).get("records", [])

    def _fetch_mock(self, **q):
        if not os.path.exists(FIXTURE):
            return []
        with open(FIXTURE, encoding="utf-8") as f:
            recs = json.load(f)
        name = q.get("name")
        return [r for r in recs if not name or name.lower() in r["personName"].lower()]
