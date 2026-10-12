import json
import sys
import uuid

import requests


#地址
BASE_URL = "http://127.0.0.1:8000/~/id-in/cse-in"

# 身份和资源名称
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
    print("\n测试应用注册成功。")
elif status in (403, 409):
    print("\n测试应用已存在，继续使用它。")
else:
    print("\n无法创建或确认测试应用，程序停止。")
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
    print("\ntemperature 容器创建成功。")
elif status in (403, 409):
    print("\ntemperature 容器已存在，继续使用它。")
else:
    print("\n无法创建或确认 temperature 容器，程序停止。")
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
    print("\n无法写入温度数据，程序停止。")
    sys.exit(1)

print("\n模拟温度数据写入成功。")



status, _ = send_request(
    "GET",
    f"{BASE_URL}/{AE_NAME}/{CONTAINER_NAME}/la",
    AE_ORIGINATOR,
)

if status != 200:
    print("\n无法读取最新温度数据，程序停止。")
    sys.exit(1)

print("\n完成：已确认 AE 和容器存在，写入并读回一条温度数据。")