# 本地火灾模拟器、Dashboard 与 MQTTX

这是一套用于联调的简化工厂模型，不是经过标定的火灾 CFD 模型。模拟器、网页、Broker
和本地演示 CSE 均可在 Windows / Python 3.13 上运行，不需要 Docker。
本模块的 demo 放在 Selina 负责的目录中，未修改队友的 simulation/、twin/ 或 cse-config/。

## 运行方式

使用 Python 3.13，在本目录运行统一入口。默认是团队文件回放，不启动 Broker、bridge 或 CSE：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-demo.ps1 -Mode Recorded
```

打开 http://127.0.0.1:8765/#recorded，上传或使用当前 simulation 目录的记录。该模式用 requirements.txt；首次运行自动安装缺少的依赖。Ctrl+C 关闭网页服务。

| Mode 参数 | 用途 | 依赖 | 边界 |
|---|---|---|---|
| Recorded（默认） | 上传 JSON/JSONL、回放、drone 规划、导出 robot 任务建议 | requirements.txt | 离线，不发设备指令 |
| SwarmHeadless | Sariel 原脚本的 random/swarm 基准及 JSONL 导出 | requirements-simulation.txt | 三架 drone；会重新生成 simulation 中的两个输出文件 |
| Swarm | 同上，随后打开二维动画 | requirements-simulation.txt | 需要图形桌面；动画前先跑 benchmark |
| LocalDemo | 旧工厂四 agent 演示及本地 MQTT | requirements-demo.txt | 独立示例，不是团队 oneM2M 接入链路 |
| LocalCse | 旧工厂演示＋临时本地 ACME A/B/IN | requirements-demo.txt | 验证隔离的 MQTT/HTTP 示例；不是团队 Mobius 环境 |

例如 `powershell -ExecutionPolicy Bypass -File .\start-demo.ps1 -Mode SwarmHeadless`。`-CheckOnly` 只检查并准备所选模式的依赖。`-WithoutCse` 兼容旧用法，相当于 LocalDemo。

团队接入仅使用 oneM2M MQTT binding，见后面的交接说明及 native-import。不要向团队 Broker 发布普通 rescuetwin topic，也不要为它启动 bridge/provision。

以下 MQTTX 普通 topic 章节仅对应 LocalDemo/LocalCse 的旧隔离演示。其端口为 18883；团队 Broker 则是 10.70.80.245:1883、需要账号，并使用 /oneM2M 请求/响应。

## MQTTX 逐项配置

MQTTX 是客户端，必须先启动上面的 Broker。安装 MQTTX 本身不会创建 Broker。

### 终端成功提示与 MQTTX 连接状态

运行 publish 后出现 `MQTT broker acknowledged publication` 是成功提示，表示 Broker
已确认收到该消息。新的提示会明确以 `SUCCESS` 开头。这条命令使用自己的短连接，发送后退出；
MQTTX 使用另一条独立连接，需要在 MQTTX 中点击右上角 **Connect（▶）**。

若显示 `Client not connected`，先选中左侧 **RescueTwins Local**，确认地址
`127.0.0.1:18883`，再点击 Connect。连接成功后，右上角按钮悬停会显示 **Disconnect**，
消息计数会随订阅数据增加。保存连接配置、订阅列表或 JSON 草稿，都不代表连接已建立。

发送前选 JSON、QoS 1、Retain 关闭。测试时间必须更新为当前 UTC；未来时间会显示
`Future timestamp`，这属于数据新鲜度提示，与 Broker 连接是两个问题。

1. 点击 New Connection / 新建连接（通常是 `+`）。
2. 填写以下内容，点击 Connect / 连接。

| MQTTX 字段 | 本地演示值 |
|---|---|
| Name | RescueTwins Local |
| Client ID | mqttx-rescuetwins-selina（与其他客户端保持不同） |
| Host / 协议 | mqtt:// |
| Host / 地址 | 127.0.0.1 |
| Port | 18883（不是 1883，也不是网页端口 8765） |
| MQTT Version | 3.1.1 |
| Username / Password | 留空 |
| SSL/TLS | 本地演示关闭 |
| Clean Session | 开启 |
| Keep Alive | 60 秒 |

如果 Host 是单个文本框，则填 `mqtt://127.0.0.1`；如果协议和地址分开，按上表填写。

3. 连接成功后，点击 New Subscription / 新建订阅。
4. Topic 填 `rescuetwin/#`，QoS 选 `1`。保存后，会看到三个节点、六类读数；模拟器发送变化值和定期心跳，积压时降低普通读数频率。
5. 再订阅 `rescuetwin-ack/#`（CSE 保存成功的 ACK）。
6. 如需 agent 坐标和跨区事件，再订阅 `rescuetwin-sim/#`。

| Topic | 用途 |
|---|---|
| rescuetwin/A/node-01/temperature | A 区 node-01 温度 |
| rescuetwin/A/node-02/smoke_raw | A 区 node-02 烟雾 mV |
| rescuetwin/B/node-03/temperature | B 区 node-03 温度 |
| rescuetwin-ack/A/node-01 | Bridge 已将记录写入 CSE 的通知 |
| rescuetwin-sim/agents/agent-01 | 自有模拟坐标，单位 m；不是 MEC013 API 响应 |
| rescuetwin-sim/events | 本地 geofence 跨区事件；不是 MEC021 完成通知 |

## 在 MQTTX 手动发送一次

发送区域 Topic 填 `rescuetwin/A/node-01/temperature`，QoS `1`，Retain **关闭**，Payload 选 JSON：

```json
{
  "device_id": "node-01",
  "zone_id": "A",
  "sensor_type": "temperature",
  "value": 65.5,
  "unit": "degC",
  "source": "simulation",
  "quality": "valid",
  "observed_at": "2026-10-16T08:30:00Z"
}
```

把 `observed_at` 换成发送时的 UTC 时间；每次测试更新，避免被去重或显示为过期。
点击发送后，你在 `rescuetwin/#` 订阅中应收到自己的消息。由于模拟器每秒持续发送
node-01 的数据，最新值稍后会被下一次模拟读数更新，这属正常现象。

如果要生成最新时间的消息，也可在第二个终端执行：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin publish sample-temperature.json --now
```

只有真实设备读数才使用 `source: physical`。`value` 用 JSON 数字而非字符串；`presence`
使用 true/false。消息必须恰好包含约定八个字段，device/zone/sensor 必须与 topic 一致。

## 玩一次完整场景

1. Dashboard 点击 **Run Fire Demo**：重置地图，在 A 区设置火源并运行。
2. 观察火源周边温度和烟雾向邻格变化，墙体阻隔扩散。
3. 切换 **Temperature / Smoke / Layout** 查看地图；agent 使用内置 A* 避开危险格子。
4. MQTTX 中过滤 node-01 temperature/smoke_raw，查看与地图一致的模拟读数。
5. Dashboard 的 **Readings Stored in CSE** 应持续增加；可直接读取 CSE 验证：

```powershell
$readHeaders = @{
  'X-M2M-Origin' = 'CNode01'
  'X-M2M-RI' = [guid]::NewGuid().ToString('N')
  'X-M2M-RVI' = '4'
  'Accept' = 'application/json'
}
$latestReading = Invoke-RestMethod -Uri 'http://127.0.0.1:18001/~/id-mn-a/cse-mn-a/node-01/temperature/la' -Headers $readHeaders
$latestReading.'m2m:cin'.con | ConvertFrom-Json
```

6. 通过 **Map Click Action** 设置新火源、修改障碍或给选中 agent 设置新目标。
7. agent 穿越 x=50m 边界后，经 2m 缓冲及连续两次确认，记录跨区事件。
8. 导出传感器 CSV 或状态 JSON 作为联调证据。

## Dashboard 页面与地图操作

侧栏与窄屏顶部导航均可切换以下三个独立页面：

- **Factory Overview**：实时 3D 地图、2D Grid、温度/烟雾图层、火源与障碍编辑、趋势和事件。
- **Sensors & Messages**：按设备、区域、来源、传感器、质量和新鲜度筛选读数；Inspect 查看原始
  八字段 JSON，Copy JSON 复制，Export Filtered CSV 导出筛选后的最新读数。消息日志在当前浏览器
  缓存最近 500 条；Pause Log 只暂停此缓存，不停止 MQTT 或模拟器。重新打开网页会清空浏览器缓存。
- **Agent Status**：筛选和选择 agent，查看区域、状态、当前位置、目标、行程、重规划次数、
  局部风险和路线。目标坐标使用米并吸附到 2m 网格中心；Apply Goal 提交，暂停时需 Resume 才移动。
  Choose Goal on Map 切回地图选目标，Locate in 3D 将镜头移到所选 agent。

3D 地图的鼠标操作：左键拖动旋转；**按住鼠标中间滚轮拖动平移**；滚动滚轮缩放；右键拖动也可
平移。无中键的触控板可用 Shift + 左键拖动平移。**Reset View** 恢复默认视角。单击地面执行
Map Click Action，拖动镜头不会放置火源。右下角按钮可旋转、缩放。WebGL 不可用时可切换 2D Grid。

3D 建筑、火焰与烟雾是同一二维模型的可视化；未增加三维火灾物理。网页源码修改与构建方式见
[模块 README](../README.md#修改网页)。已有服务通常只需刷新网页；Python 服务修改后需先在原终端
Ctrl+C，再重新运行 start-demo.ps1，避免同时启动两个 Bridge。

## Dashboard 指标含义

- 地图温度/烟雾来自模型，不能代表真实工厂测量值。烟雾指数为归一化模拟强度。
- MQTT 接收数来自真实订阅回调。PUBACK 表示 Broker 确认，包含模拟器发送的遥测和坐标等消息。
- CSE 保存数来自 Bridge outbox 的 delivered 状态，跨程序重启累计；地图重置不会删除 CSE 历史。
- physical 与 simulation 分行显示。新鲜有效的外部 MQTT 告警会添加传感器危险排除区并触发 inspection task；不会注入虚假的火焰或改写物理模型温度。
- 传感器表按接收的各类最新读数展示；缺失/invalid 不作为有效值，过期时间单独标记。
- Overview 的历史 CSV 最多保留本次运行最近 10,000 条有效遥测；Sensors 页的 CSV 是当前筛选后的
  最新读数。趋势保留 180 个模拟秒采样。
- 暂停仅暂停环境演变，遥测仍按变化值与心跳发送；1×/2×/4×只影响模拟时间。

## 模型和集成边界

50×30 网格（每格 2m），固定随机种子 7，固定 0.25s 步长。热量/烟雾在非墙体四邻格
之间离散扩散并衰减，火源持续注入，邻格按简化规则蔓延。无燃料耗尽、通风、燃烧化学、
建筑热工或真实 CO 浓度模型；mV 为示例模拟值，不可换算成 ppm。
路径规划结合模拟地图与简单传感器阈值产生的危险排除区；传感器影响范围是假设值，路径示例仍不是队友的 swarm 算法。

这版不向公共 Sandbox 注入 UE 位置，不将 Monaco UE 自动映射为工厂 agent，也不调用
MEC021。后续由 MEC-IPE 对接实际 MEC 服务、由队友算法与 dashboard/资源接口集成。

## 下一阶段建议顺序

1. **先稳定数据链路**：持续运行并观察 CSE pending 是否增长，区分接收速率与写入速率；测量
   observation → MQTT → CIN → 路线更新的时间，处理重试、积压、去重和历史保留。当前 SQLite
   已有状态索引，但不能据此认为 CSE 吞吐问题解决；不要删除 outbox 来掩盖积压。
2. **接入真实硬件与算法**：接入 ESP32 的 physical 读数，明确节点与地图坐标的对应关系；与队友
   约定危险地图和 agent 指令/回执接口，使告警、派单、重规划、完成状态形成闭环。现有内置 A*
   已结合传感器阈值产生的危险排除区规划，并可以触发本地 inspection 任务；真实 ESP32 与队友 swarm 仍需联调。
3. **完成实际 MEC/oneM2M 联调**：拿到 shared VM/两个 MEC host 的实际访问参数，部署 MN-A/B
   和应用；验证真实 Location/RNIS、MEC-IPE 双向通信、MN→IN 注册与路由。区域事件与 CSE 切换、
   订阅连续性、MEC021 应用状态迁移应分别验收；本地跨区动画不能证明这些已完成。
4. **补齐演示证据**：固定场景与随机种子，对比无火灾/有火灾、正常网络/延迟丢包、直接 MN/经 IN，
   导出 P50/P95 延迟、成功率、丢失记录、重规划次数和到达时间；为告警加确认、恢复与事件编号。
5. **部署与展示完善**：在实际主机验收 CSE HTTPS、MQTT mTLS/设备 ACL 和重启恢复；远程 Dashboard
   再配置登录和访问控制。最后再增加场景保存/加载、回放、楼层与设备模型，避免先堆装饰性 3D 效果。

## 常见问题

- Client not connected：MQTTX 自己尚未连接；点击右上角 Connect，不需要修改八字段 JSON 来解决连接问题。
- JSON SyntaxError：在消息编辑框内 Ctrl+A 后粘贴完整 JSON，检查没有多余的结束括号或其他字符。
- 看不到新消息：清除顶部 Topic/Message 搜索条件，并滚动到消息列表底部；旧消息会保留。
- Connection refused：Broker 未运行、终端已关闭，或把端口填成 1883。
- Port already in use：已有一次 demo/Compose 在运行；复用它或在原终端 Ctrl+C 停止。
- MQTT 收到但 CSE pending 增加：检查 A/B 的 CSE 和 AE identity，不要只看 MQTTX 连接状态。
- CSE quarantined 增加：通常为格式/权限问题，检查 runtime/demo/bridge.sqlite 和终端输出。
- 无消息：先订阅 `rescuetwin/#`，确认不是 `rescuetwins/#`，并保留启动终端。
- 外部设备连接不上 127.0.0.1：该地址只代表当前电脑。硬件接入需要另行配置可达且带认证的 Broker。

MQTTX 官方说明：https://mqttx.app/docs


## CSE backlog recovery and rescue loop

Restart the existing launcher once to load the new Python service. Stop the old terminal with Ctrl+C first,
then run `powershell -ExecutionPolicy Bypass -File .\start-demo.ps1`. Do not run two copies.
The outbox is migrated in place; historical records are kept. No queue or CSE history is deleted.
The old queue takes time to replay. The Overview now shows **CSE Delivery Health**: waiting/in-flight records,
oldest wait, incoming and stored records/s, and the recent delivery error. A positive net drain indicates
that delivery is outpacing ingestion; rates need a ten-second warm-up. The on-disk `delivered` total is cumulative.

Changes in this version:

- MQTT ingestion commits batches with SQLite WAL and synchronous FULL before acknowledging QoS 1.
- One delivery worker per MN-CSE isolates A/B failures. Claims and completion updates are batched; an OS lock
  prevents two bridges from owning the same outbox. Interrupted claims are replayed on restart.
- CIN names remain deterministic for safe HTTP retries. Existing eight-field telemetry containers are unchanged.
- Simulator values are published on meaningful changes and a five-second heartbeat. With 200+ queued records,
  ordinary changes are limited to once per five seconds and the heartbeat becomes fifteen seconds. Alarm/clear
  threshold crossings bypass that delay. External physical publishers are never sampled or discarded.
- Rescue events have delivery priority over telemetry backfill and are stored under
  `<MN_BASE_URL>/rescue-control/events` using originator `CRescueControl`.

### Run the loop

1. Open Overview and click **Run Rescue Loop**. This starts a repeatable simulated fire at node-01.
2. In **Rescue Operations**, follow `incident opened → task dispatched → task accepted → task moving → task completed`.
3. A red exclusion area is added near the reporting sensor. The local A* executor routes to a safe inspection
   staging cell outside that area. If there is no safe route, the system reports blocked status.
4. Click **Acknowledge** to record operator acknowledgment. **Dispatch again** requests a fresh inspection.
5. Clear Fires and let sensor readings cool below the recovery thresholds. Each triggered channel needs two fresh
   clear samples; the incident then resolves and its exclusion area is removed. Inspection completion alone
   does not resolve the incident. Reset closes simulated incidents explicitly; it preserves physical alerts.

For actual hardware, retain the agreed eight-field MQTT contract and set `source` to `physical` only for real
measurements. `rescue.sensor_positions` maps each device to factory coordinates in metres. Defaults use
60/50 degC for temperature trigger/clear, 2500/2000 mV for smoke, and 2300/1800 mV for CO. These are
uncalibrated demo thresholds. Invalid, duplicate, older, future (>5s), or stale (>35s) observations cannot start
or clear an alert. A stale active alert stays active until fresh recovery data arrives.

MQTTX can inspect these additional topics (QoS 1, retain off):

| Topic | Meaning |
|---|---|
| `rescuetwin-control/commands/+` | Inspection command, with command_id/task_id/incident_id/agent_id/goal |
| `rescuetwin-control/feedback/+` | Accepted, moving, blocked, completed or failed receipt with the same IDs |
| `rescuetwin-control/events` | Incident and task timeline |

The controller state and timeline persist in `runtime/demo/rescue.sqlite`. After service restart, unfinished
local tasks are cancelled because the simulator resets its agents. Use Dispatch again for a still-active alert.
Commands retry until acknowledged (30s timeout). Execution has a 120 simulated-second timeout; pausing the
simulation pauses that execution timer. A manual goal cancels that agent's current automated task.

The active executor is **local_simulation**. This implements MQTT command/receipt integration and CSE audit
storage; it does not control an ESP32 actuator or replace the team's swarm algorithm. Replace `LocalExecutor`
with the teammate/robot adapter, preserving the command and feedback IDs, before claiming real robot control.
The loop currently uses live MQTT observations for prompt reactions while CSE writes proceed asynchronously.
During historical replay, CSE `/la` can lag; inspect the reading's `observed_at` before using it for decisions.
The local MQTT broker permits these topics; production mTLS/ACL roles for the controller/executor still need
separate deployment configuration and acceptance testing.

### Local CSE startup and shutdown

Use Ctrl+C in the demo terminal for a normal stop. The launcher asks each local CSE to flush its database
before exiting. `stop-demo.ps1` remains a forced recovery option for a frozen process.

If a local CSE fails to start, inspect both `runtime/demo/<zone>.log` and
`runtime/demo/<zone>/logs/`. ACME errors are now saved even in headless mode. Do not delete the queue or
the CSE data folders to work around a JSON database error; preserve the files before attempting recovery.


## Recorded JSON integration and drone missions

Use this mode for the team files currently uploaded by hand. In `mec-app/deploy`, run:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m rescuetwin.demo --config config.team.example.json --recorded-only
```

Open http://127.0.0.1:8765/#recorded. This starts the local dashboard without a broker or bridge. It does not connect to the team broker or write to Zihan's CSEs. The original Overview is a separate illustrative 50x30 model; **Recorded Mission uses Sariel's actual 30x20 grid** and does not mix their coordinates.

1. The page initially reads `simulation/sim_agent_log.jsonl` and `simulation/sim_sensor_data.jsonl` without editing them. Upload replacement sensor or agent files using the two file selectors. JSON objects, arrays, JSONL and a `m2m:cin.con` sensor wrapper are accepted, up to 4 MiB. A malformed agent file leaves the previous dataset intact. Data stays in memory for this session; export the mission before restarting.
2. Move the replay slider. Only positions and victim events at/before that step are visible to the planner. Original UTC times and `physical`/`simulation` provenance stay intact; historical physical data is not labelled live.
3. Select a drone and optionally enter a target in **grid cells**. With no target, choose an already reported victim or a patrol waypoint. The planner checks walls/configured hazard cells, keeps a return route, and reserves 10 simulated energy units (one per travelled cell). Insufficient energy produces return-home or blocked status. This is a route preview with no altitude/flight dynamics or real battery model.
4. Simulate the round trip and export Mission JSON. This never sends flight commands or CSE registration requests. Robot tasks are suggestions with no assigned robot; a robot task/acknowledgment adapter is still required. Replay, upload, or a new plan invalidates the previous execution reference.

### Current file compatibility

- `sim_agent_log.jsonl`: 280 entries: 3 nodes, 267 drone positions, 3 victim reports, 7 recorded zone handovers. The log has `drone-A`, `drone-B`, `drone-C`; no separately identified ground robot. These are recorded movements, not proof of CSE or MEC021 handover.
- `sim_sensor_data.jsonl`: 267 smoke entries in `mV`, covering A/B/C. All can be inspected as recorded data, but 178 A/B records match the updated mV sensor contract; the 89 zone-C records still have no CSE mapping. Do not silently send C to MN-B.
- `node01_sample.jsonl`: 216 historical physical readings, all 216 now match the agreed mV fields. Compatible rows may be inspected, but uploads are not a claim of current device connectivity.
- `sample_data.json`: existing 25-row file remains supported. The agreed mV contract accepts 24 rows; one missing timestamp remains rejected.
- Smoke mV has no agreed hazard threshold. It remains unclassified by the drone planner. Only explicit unit-matched thresholds are used. Do not interpret a wall-clear route as a certified fire-safe flight.

### Team CSE access and independent identities

`config.team.example.json` records the supplied endpoints (IP `10.70.80.245`; MN-A 8001, MN-B 8002, Mobius 7599) with explicit no-HTTP-auth configuration. It keeps the existing TLS production template unchanged. The team profile disables HTTP writes, provisioning and the raw MQTT-to-HTTP bridge. Explicit native-import --send permits only the configured node-01 sensor upload through oneM2M binding. Existing originators are used only in the two documented read-only checks.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m rescuetwin --config config.team.example.json check-team
```

This writes a local result to `runtime/team-check.json`, returns a failing exit code for incomplete checks, and sends only GET requests. On 2026-11-10, the Location check succeeded; both Mobius transit reads timed out from this computer. Direct MN-A/MN-B checks also timed out. Recheck current IP, same-LAN/VPN routing, service bind address, Windows firewall and running services with Zihan. His localhost verification is separate from connectivity on Selina's computer.

The team broker uses **oneM2M MQTT binding** at `10.70.80.245:1883`. The `native_mqtt` section documents the two supplied accounts and password environment variable names; no passwords are stored. The local dashboard broker remains separate. It is not a bridge into those CSEs. Request/response topics must follow the supplied `/oneM2M/...` structure, with a unique MQTT Client ID and matching `rqi`/successful `rsc`. Independent drone/MEC AEs, their ACPs and a dedicated MQTT account/ACL must be agreed before enabling writes. Do not use the physical node's account for another application. No topic ACL isolation or TLS is asserted for the team's current broker.

### MEC endpoint and IDs

Live service discovery and GET checks on 2026-11-10 confirmed Location at `https://82.112.253.59/sbx42843tk/mep1/location/v3/queries/users` (200); v2 returned 404. The verified v3 root is saved in the team profile. HTTPS validation remains enabled; `system_trust` uses OS trusted roots. Discovered addresses were `10.10.0.1`, `10.1.0.1`, `10.100.0.1`. No address is automatically mapped to a drone or to grid zone A/B/C.

`298b2c0c-7efa-45d3-8b47-8ab3c009b845` belongs to the **Location provider**; `f8a0d2ab-9977-4d71-8c4f-354b4d59fb32` is its service ID. The RescueTwins consumer `app_instance_id` remains empty. Obtain it by creating your own application instance. MEC021 registration, externally reachable callback, two-instance migration and live CSE writes remain pending. JSON upload/replay itself needs no public callback.

Still agree with the team: zone C's CSE routing, mV calibration or unit-specific thresholds, per-drone AE/ACP and broker permissions, UE-to-agent mapping and a coordinate transform, and the real robot's task/feedback interface. Keep these explicit; changing only a zone label does not implement handover.

+
+### 本轮 mV 与运行方式验证（2026-11-10）
+
+- 完整 Python 回归：58 passed，3 项本地 ACME 启动/HTTP 超时。停止较重的并行任务后，三项失败测试与 native-import 测试一并复查，8 passed，未修改测试超时或跳过断言。
+- 新增直接 oneM2M MQTT 上传 mV 记录并通过 HTTP 精确读回的本地测试通过；未连接团队 Broker 写入。一次性 native MQTT 客户端使用唯一 Client ID 和临时会话，断开后不保留无用订阅。
+- 前端模型测试 7 passed；Edge 浏览器上传、时间轴、drone 规划/往返、JSON 下载、旧页面和手机布局检查通过。
+- Sariel 原始代码在临时副本运行完整 20 轮 random/swarm 基准，约 58 秒完成，导出 267 条传感器和 280 条 agent 日志。绘图和实际动画回调的前 5 帧检查通过；图形桌面窗口需要组员本地打开。
+- Recorded、SwarmHeadless、LocalCse 依赖入口检查通过。Recorded 的独立网页启动通过；LocalDemo 的本地 MQTT 消息检查通过。LocalCse 的底层真实 ACME/MQTT 互通在回归中验证，未把它视为团队 Mobius 联通。
+- 队友目录 14 个原有文件校验值保持不变。smoke/co 为 mV；有效电压值支持非负小数，无 ADC 计数限制。quality=invalid 的原始无效读数仍按现有规则保留，不冒充有效测量。
+- Hosted Location/RNIS/MEC011 GET 成功；通过团队 Mobius 读取仍超时，MQTT 密码和独立 drone/robot 权限仍待实际环境配置。
+