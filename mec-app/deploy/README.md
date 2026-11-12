> 2026-11-11 当前版本已改为 50×30、A/B 两区、drone-A/B，并增加独立 MEC swarm-fusion 服务。当前启动与部署以 [README_RUN.md](README_RUN.md) 为准；下文早期三分区说明保留为历史记录。

> 简单启动方法：请先看 [Simulation / Dashboard 快速启动](README_RUN.md)。

> 录制数据输入与验收操作见 [部署指南](docs/deployment-guide.md)。默认 sensor 数据来自根目录 `simulation/sample_data.json`，保留原时间与 source；不兼容记录单独报告。

# RescueTwins MQTT / MEC Integration

本模块包含团队 oneM2M MQTT 文件上传、MEC 接入、drone 记录回放和路线预览，以及隔离的本地工厂演示。
传感器消息使用团队约定的八个字段。

Dashboard 包含 Recorded Mission、Factory Overview、Sensors & Messages 和 Agent Status 四个页面，支持
传感器筛选、JSON 检查、CSV 导出、agent 路线与目标编辑。3D 地图使用同一套二维模拟数据。

## 本地启动（不需要 Docker）

在本目录的 PowerShell 中运行：

~~~powershell
powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
~~~

打开 http://127.0.0.1:8765/#recorded。默认只启动文件回放网页，不启动 MQTT Broker、bridge 或 CSE。

启动参数 `-Mode Recorded|Swarm|SwarmHeadless|LocalDemo|LocalCse` 对应文件回放、Sariel 动画、无动画基准、旧本地演示、旧本地 CSE 演示。详细命令、依赖、API 请求/响应和未完成项见 [组员交接说明](docs/team-collaboration.md#2026-11-10-selina-交接)。

团队链路直接使用 oneM2M MQTT binding；普通 rescuetwin topic 与 bridge 仅保留用于旧隔离演示。smoke_raw/co_raw 已统一为 mV，原始数值不换算。

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


## Team recordings and drone integration

Use [the recorded mission workflow](docs/simulator-mqttx-guide.md#recorded-json-integration-and-drone-missions) for uploaded sensor JSON/JSONL and the current `simulation/sim_agent_log.jsonl`. It preserves the original three-zone grid and timestamps, adds offline drone inspection/patrol routing, round-trip/energy checks, and exports robot task suggestions. Launch with `python -m rescuetwin.demo --config config.team.example.json --recorded-only`, then open `http://127.0.0.1:8765/#recorded`.

`python -m rescuetwin --config config.team.example.json check-team` checks the supplied Mobius transit reads and MEC013 without registration or writes. Team CSE access from this computer is still unverified (timeouts on 2026-11-10); MEC Location v3 was reachable. Smoke/CO mV units are aligned; hazard thresholds, zone C routing, independent AE/ACP permissions, robot feedback and UE mapping remain explicit integration gaps. This feature does not claim real-time flight or live migration.


## Swarm cooperative control

Swarm exploration bidding and collision reservations are implemented in `rescuetwin/swarm_logic.py`, served by `rescuetwin/swarm_service.py` and consumed through `tools/run_swarm.py`. The existing Swarm launcher uses this adapter; the team-owned simulator remains unchanged. See [run guide](README_RUN.md#swarm-logic-实现位置与范围).
