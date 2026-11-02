from __future__ import annotations

import json
import os
import uuid
from urllib.parse import quote

import requests

from .core import canonical, digest


class CseError(RuntimeError):
    def __init__(self, status, rsc):
        self.status, self.rsc = status, rsc
        super().__init__(f"CSE request failed: HTTP {status}, oneM2M RSC {rsc}")


class Cse:
    def __init__(self, endpoint, originator, session=None):
        self.base = endpoint["base_url"].rstrip("/")
        self.originator = originator
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.verify = endpoint.get("ca_file") or True
        self.cert = (endpoint["cert_file"], endpoint["key_file"]) if endpoint.get("cert_file") else None
        self.token = os.getenv(endpoint.get("token_env", "CSE_TOKEN"))
        self.timeout = endpoint.get("timeout_s", 8)

    def call(self, method, path="", body=None, ty=None, allowed=(200, 201)):
        headers = {"X-M2M-Origin": self.originator, "X-M2M-RI": uuid.uuid4().hex,
                   "X-M2M-RVI": "4", "Accept": "application/json",
                   "Content-Type": "application/json" + (f";ty={ty}" if ty else "")}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self.session.request(method, self.base + path, json=body, headers=headers,
                                        timeout=self.timeout, verify=self.verify, cert=self.cert,
                                        allow_redirects=False)
        rsc = response.headers.get("X-M2M-RSC")
        if response.status_code not in allowed or not rsc or not 2000 <= int(rsc) < 3000:
            raise CseError(response.status_code, rsc)
        return response.json()

    def ensure(self, parent, name, ty, attrs, wrapper):
        path = parent + "/" + quote(name, safe="")
        try:
            data = self.call("GET", path)[wrapper]
        except CseError as exc:
            if exc.status != 404:
                raise
            try:
                data = self.call("POST", parent, {wrapper: dict(attrs, rn=name)}, ty)[wrapper]
            except CseError as conflict:
                if conflict.status != 409:
                    raise
                data = self.call("GET", path)[wrapper]
        if data.get("rn") != name or (ty == 2 and data.get("aei") != self.originator):
            raise ValueError(f"existing resource identity differs: {name}")
        return data

    def ensure_ae(self, name, containers):
        self.ensure("", name, 2, {"api": "NRescueTwin.integration", "rr": False, "srv": ["4"]}, "m2m:ae")
        for container in containers:
            self.ensure("/" + name, container, 3, {"mni": 1000}, "m2m:cnt")

    def cin(self, path, value, key=None):
        # A stable resource name makes a retry safe after a lost HTTP response.
        name = "cin-" + (key or digest(value))[:40]
        text = canonical(value)
        try:
            return self.call("POST", path, {"m2m:cin": {"rn": name, "cnf": "application/json:0", "con": text}}, 4)["m2m:cin"]
        except CseError as exc:
            if exc.status != 409:
                raise
            found = self.call("GET", path + "/" + name)["m2m:cin"]
            if json.loads(found["con"]) != value:
                raise ValueError("CIN resource name conflict with different content")
            return found

    def latest(self, path):
        return json.loads(self.call("GET", path + "/la")["m2m:cin"]["con"])

    def entries(self, path):
        # Discover all retained command CINs; reading only /la loses queued commands.
        refs = self.call("GET", path + "?fu=1&ty=4&drt=1").get("m2m:uril", [])
        for ref in refs:
            # Discovery URIs may be SP-relative or CSE-relative. Last segment is RN.
            name = ref.rstrip("/").split("/")[-1]
            yield self.call("GET", path + "/" + quote(name, safe=""))["m2m:cin"]
