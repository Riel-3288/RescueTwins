> 录制数据输入与验收操作见 [部署指南](docs/deployment-guide.md)。默认 sensor 数据来自根目录 `simulation/sample_data.json`，保留原时间与 source；不兼容记录单独报告。

# RescueTwins MQTT / MEC Integration

本模块包含 MQTT Bridge、MEC-IPE、部署模板，以及本地工厂模拟器和英文 2D/3D Dashboard。
传感器消息使用团队约定的八个字段。

Dashboard 包含 Factory Overview、Sensors & Messages 和 Agent Status 三个页面，支持
传感器筛选、JSON 检查、CSV 导出、agent 路线与目标编辑。3D 地图使用同一套二维模拟数据。

## 本地启动（不需要 Docker）

在本目录的 PowerShell 中运行：

~~~powershell
powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
~~~

打开 http://127.0.0.1:8765 。默认启动本机 MQTT Broker（18883）、ACME A/B/IN 与 Bridge。
保留启动终端；Ctrl+C 停止服务。

MQTTX 连接 mqtt://127.0.0.1:18883，MQTT 3.1.1，用户名/密码留空，本机开发连接不启用 TLS。
点击右上角 **Connect**，确认连接建立后，再订阅 rescuetwin/#（QoS 1）。

## 指南

- [Simulator + MQTTX：启动、地图、测试消息和排错](docs/simulator-mqttx-guide.md)
- [部署：MQTT 数据约定、CSE、MEC/Sandbox、TLS、Kubernetes 与验收](docs/deployment-guide.md)
- [团队协作：分支、拉取与接口交接](docs/team-collaboration.md)

## 测试

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
~~~

本地测试使用真实 MQTT/ACME，MEC 服务使用接口测试样例。Shared 环境、真实 ESP32、
ACME–Mobius 与完整 MEC021 仍需实际联调。

## 修改网页

运行演示无需 Node.js；仓库包含构建好的 `dashboard/app.js`。修改 `dashboard/src/`
后，在 `dashboard/` 中运行以下命令并刷新浏览器；不要直接编辑生成的 `app.js`：

~~~powershell
npm ci
npm test
npm run build
~~~

Three.js 已打包到本地文件，运行时无需 CDN。第三方许可见 `dashboard/THIRD-PARTY-LICENSES.txt`。

## 目录

~~~text
rescuetwin/          MQTT Bridge、oneM2M client、MEC-IPE、模拟器与 HTTP 服务
dashboard/          英文网页界面
broker/             Mosquitto 配置和 ACL
k8s/                MEC 主机部署模板
scenario/           工厂场景与地图
tools/              CSE、证书、场景及部署工具
tests/              MQTT、CSE、TLS 与模拟器测试
docs/               三份操作指南
start-demo.ps1      Windows 本地启动入口
compose.local.yaml  Docker Compose 开发环境
~~~


## Rescue loop and CSE recovery

Click **Run Rescue Loop** on Overview to observe a sensor alert, automatic safe inspection assignment,
MQTT command acknowledgment, movement, completion, and sensor recovery. Rescue Operations shows the
incident/task timeline; CSE Delivery Health shows replay throughput and queue age. The executor is local
simulation; physical robot and teammate swarm adapters still need integration.

The bridge batches durable MQTT ingestion and completion commits, runs independent A/B delivery workers,
prioritizes rescue events, and replays the existing outbox without deleting records. Simulator telemetry uses
change detection and adaptive heartbeats to reduce repeated writes. See the simulator guide for restart,
MQTT topics, thresholds, recovery, and current integration limits.

If an older demo freezes during task dispatch, open a new PowerShell terminal and run `stop-demo.ps1`,
then start the demo again. The helper checks command-line ownership of the demo ports before stopping
anything and keeps all database files. Use `-WhatIf` to preview its targets. MQTT publication now runs
on a separate worker so broker callbacks cannot deadlock the simulation lock.
