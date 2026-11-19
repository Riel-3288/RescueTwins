import json
import sys
import uuid

import requests


# CSE base URL
BASE_URL = "http://127.0.0.1:8000/~/id-in/cse-in"

# Identity and resource names
AE_ORIGINATOR = "CRescueTwinTest"
AE_NAME = "rescuetwin-test"
CONTAINER_NAME = "temperature"


def make_headers(originator, resource_type=None):
    content_type = "application/json"

    if resource_type is not None:
        content_type += f";ty={resource_type}"

    return {
        "X-M2M-Origin": originator,
        "X-M2M-RI": str(uuid.uuid4()),
        "X-M2M-RVI": "4",
        "Content-Type": content_type,
        "Accept": "application/json",
    }


def send_request(method, url, originator, resource_type=None, body=None):
    response = requests.request(
        method=method,
        url=url,
        headers=make_headers(originator, resource_type),
        json=body,
        timeout=10,
    )

    try:
        data = response.json()
    except ValueError:
        data = {"raw_response": response.text}

    print(f"\n{method} {url}")
    print(f"HTTP status: {response.status_code}")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    return response.status_code, data



status, data = send_request(
    "POST",
    BASE_URL,
    AE_ORIGINATOR,
    resource_type=2,
    body={
        "m2m:ae": {
            "rn": AE_NAME,
            "api": "NRescueTwin.test",
            "rr": True,
            "srv": ["4"],
        }
    },
)

if status == 201:
    print("\nTest application registered successfully.")
elif status in (403, 409):
    print("\nTest application already exists. Reusing it.")
else:
    print("\nCannot create or confirm the test application. Stopping.")
    sys.exit(1)



status, _ = send_request(
    "POST",
    f"{BASE_URL}/{AE_NAME}",
    AE_ORIGINATOR,
    resource_type=3,
    body={
        "m2m:cnt": {
            "rn": CONTAINER_NAME,
            "mni": 20,
        }
    },
)

if status == 201:
    print("\nTemperature container created successfully.")
elif status in (403, 409):
    print("\nTemperature container already exists. Reusing it.")
else:
    print("\nCannot create or confirm the temperature container. Stopping.")
    sys.exit(1)



temperature_reading = {
    "device_id": "node-01",
    "zone_id": "A",
    "sensor_type": "temperature",
    "value": 28.6,
    "unit": "degC",
    "source": "simulation",
    "quality": "valid",
}



status, _ = send_request(
    "POST",
    f"{BASE_URL}/{AE_NAME}/{CONTAINER_NAME}",
    AE_ORIGINATOR,
    resource_type=4,
    body={
        "m2m:cin": {
            "con": json.dumps(temperature_reading),
        }
    },
)

if status != 201:
    print("\nCannot write temperature data. Stopping.")
    sys.exit(1)

print("\nSimulated temperature data written successfully.")



status, _ = send_request(
    "GET",
    f"{BASE_URL}/{AE_NAME}/{CONTAINER_NAME}/la",
    AE_ORIGINATOR,
)

if status != 200:
    print("\nCannot read the latest temperature data. Stopping.")
    sys.exit(1)

print("\nDone: confirmed the AE and container, then wrote and read back one temperature record.")