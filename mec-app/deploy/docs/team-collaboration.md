# 团队协作与联调

团队仓库：`https://github.com/Riel-3288/RescueTwins`。
本模块路径：`mec-app/deploy/`，功能分支：`feature/mqtt-mec-integration`。

## 获取代码

在尚未合并 PR 时，队友可以先在自己的 clone 检查功能分支：

```powershell
git fetch origin
git switch --track origin/feature/mqtt-mec-integration
```

如果该分支已经存在于本地，使用 `git switch feature/mqtt-mec-integration`。
合并后，在各自没有未提交改动的工作区更新主分支：

```powershell
git switch main
git pull --ff-only origin main
```

分支切换前保留各自的未提交工作。后续开发从更新后的 main 创建自己的分支，经 PR 合并。

## 运行模块

以下从仓库根目录开始，使用 Python 3.13：

```powershell
cd mec-app/deploy
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
```

首次运行本地演示，按 [Simulator + MQTTX 指南](simulator-mqttx-guide.md) 执行 start-demo.ps1，无需 Docker。
使用容器或共享环境时，按[部署指南](deployment-guide.md)操作。
新目录中的 `.gitignore` 和 `.dockerignore` 排除了虚拟环境、运行数据、私钥、tokens 和本地配置。

## 交给队友的接口（团队当前链路）

| 部分 | 所需信息 | 当前接口 |
|---|---|---|
| 硬件 | Broker 地址、账号密码、Originator、目标 CSE、容器路径 | oneM2M MQTT binding，先订阅响应再发布请求；八字段读数序列化到 m2m:cin.con，等待匹配 rqi 与 rsc=2001 |
| CSE | A/B/IN URL、实际注册 aei、认证与 ACP | 团队上传直接走 oneM2M MQTT；HTTP 用于已有跨 CSE 只读检查，不需要额外 bridge |
| Swarm / twin | CSE 资源路径、事件 schema、更新或订阅方式 | IPE 的 `mec-ipe/location`、`rnis`、`requests`、`responses`、`handover`；agent 的 `position` |
| MEC | 用户自己的 API base、UE address、应用 instance ID | MEC013 Location、MEC012 RNIS、MEC011 confirm_ready |

CSE 正式配置继续由 `cse-config/` 的维护者负责。本模块的 `tools/acme_config.py` 只提供独立测试/部署示例。
先验证 oneM2M MQTT→MN-A→响应/读回及 Mobius 跨 CSE 读取，再接独立 drone AE 与 MEC 位置，最后验收 MN-B/迁移。旧普通 topic 示例仅用于隔离的 LocalDemo/LocalCse。

## 当前验证边界

测试包含真实 MQTT 与 ACME CSE、Mcc 转发、HTTPS 和 MQTT client certificate 验证。
自动回归中的 MEC REST 输入来自本地测试服务；另已只读验证真实 Hosted Location/RNIS/MEC011。shared VM 部署、Mosquitto ACL、独立应用接入、ACME–Mobius
互操作、MEC021 context transfer 和完整订阅迁移仍需各自环境的验收证据。


## 2026-11-10 Selina 交接

当前仓库 origin 是 https://github.com/Riel-3288/RescueTwins.git，分支 feature/mqtt-mec-integration，已提交基线 9300fc5。本次 mV、drone 回放、统一启动入口和 native-import 更新仍是本地改动，没有 push。组员只 git pull 不会拿到这批更新；请使用本次交接 ZIP 覆盖到各自仓库根目录，或等这些改动正式提交后再拉取。ZIP 内不含密码、虚拟环境和运行数据库。

### 文件位置和依赖

- Sariel 原始仿真：simulation/rescue_sim.py；输入 node01_sample.jsonl，输出 sim_sensor_data.jsonl 和 sim_agent_log.jsonl。源码、输入和原有输出在本次修改中保持不变。
- Selina 回放/规划：mec-app/deploy/rescuetwin/recordings.py；页面在 dashboard；直接 MQTT 文件上传在 rescuetwin/native.py；团队配置是 config.team.example.json。
- Python 3.13。回放依赖 requirements.txt；原仿真依赖 requirements-simulation.txt（numpy、matplotlib）；旧本地 CSE 演示依赖 requirements-demo.txt（另外包含 aMQTT、ACME）。已生成网页 app.js，无需组员安装 Node 来启动。

### 启动方法

在项目根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Recorded
```

打开 http://127.0.0.1:8765/#recorded。默认读取现有 sensor/agent 日志，也支持上传新文件、时间轴、drone 侦察/巡逻、避障、简化电量/返航及 JSON 导出。不需要连团队电脑、Broker 或 VM。

要重新运行 Sariel 的三架 drone random/swarm 基准并输出 JSONL：

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode SwarmHeadless
```

要在基准和 JSONL 导出之后显示二维动画，将参数换成 `-Mode Swarm`。脚本原始入口为 `python simulation/rescue_sim.py --no-anim` 或不带 `--no-anim`；文件开头旧注释写的 rescuetwin_sim.py 并非实际文件名。

要演示旧四个 factory agent，使用 `-Mode LocalDemo`；要同时启动隔离的本地 ACME A/B/IN，使用 `-Mode LocalCse`。这些是旧独立示例，不是团队 Mobius/oneM2M MQTT 接入的证明。

### Robot 和 drone 的支持范围

Sariel 当前代码实例化 drone-A/B/C，并没有单独的地面 robot 类或机器人命令接收器。Recorded Mission 已适配这三架 drone 的日志。它导出的 robot_task_suggestions 是任务建议，不是已执行回执。地面 robot 还需要统一坐标、agent ID、接收任务及反馈接口；不能把页面里有 agent 就当作 robot/drone 两类都已接好。

### mV 字段与文件检查

smoke_raw、co_raw 均使用非负有限 mV 数值，支持小数，不再使用 ADC 计数的 0..4095 整数限制。temperature/degC、humidity/percent、pressure/hPa、presence/boolean 保持各自单位。更新只修改 Selina 模块，原始数据的 value/source/observed_at 不重写。

当前 sample_data.json 25 条中 24 条符合格式，1 条缺时间；node01_sample.jsonl 216 条均符合格式；sim_sensor_data.jsonl 的 A/B 178 条符合格式，C 区 89 条仍无 CSE 映射。符合格式不等于已授权写入。mV 的火情阈值仍需团队确认；旧 local factory 的数字只作为示例阈值。

### 团队 oneM2M MQTT binding

Broker 10.70.80.245:1883，当前无 TLS；node-01 账号的密码私下配置。MQTT username=node-01，AE originator=CNode01，目标 /id-mn-zone-a，三者不混用。每个连接自动生成唯一 Client ID。

- 先订阅 /oneM2M/resp/CNode01/id-mn-zone-a/json，收到 SUBACK 后再发请求。
- 请求发布到 /oneM2M/req/CNode01/id-mn-zone-a/json。
- 每次请求生成新 rqi；con 是序列化后的八字段 JSON 字符串；不改成普通 rescuetwin topic。
- 匹配 rqi 且 rsc=2001 才记录创建成功。Broker PUBACK 不等于 CSE 成功。

先在 mec-app/deploy 目录预览文件，默认不发送：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.team.example.json native-import --zone A --file ..\..\simulation\node01_sample.jsonl
```

需要上传时，在自己的终端私下设置 TEAM_NODE01_MQTT_PASSWORD，再在同一命令追加 `--send`。只允许该账号绑定的 node-01/A 传感器数据；独立 drone/MEC 身份不能冒用 CNode01。任一格式错误默认阻止整批；显式 `--skip-invalid` 才跳过不合格行。日志记录部分成功；再次 --send 会创建新 CIN，不声称全局去重。

### MEC 平台和 API

Hosted 页面：https://82.112.253.59/ 。当前个人 Sandbox=sbx42843tk，MEP=mep1。2026-11-10 只读验证下表接口均返回成功；Hosted 运行不代表已经有 shared VM 的 SSH/Kubernetes 权限，目前没有可用 shared VM 凭据。

| 用途 | 请求地址 | 当前用途 |
|---|---|---|
| MEC013 Location | GET https://82.112.253.59/sbx42843tk/mep1/location/v3/queries/users | 获取 UE 位置；支持 ?address=10.10.0.1 |
| MEC012 RNIS | GET https://82.112.253.59/sbx42843tk/mep1/rni/v2/queries/rab_info | 获取 UE/cell/RAB 信息 |
| MEC011 时间 | GET https://82.112.253.59/sbx42843tk/mep1/mec_app_support/v2/timing/current_time | 健康检查 |
| MEC011 服务发现 | GET https://82.112.253.59/sbx42843tk/mep1/mec_service_mgmt/v1/services | 获取真实服务版本及 endpoint |
| MEC021 AMS | https://82.112.253.59/sbx42843tk/mep1/amsi/v1 | 服务发现已公布；应用注册/迁移尚未验收 |

Location 的旧 v2 Try-it 地址此前返回404，以 v3 和服务发现公布的 endpoint 为准。接口请求当前使用 Accept: application/json，保持 HTTPS 证书验证；本次测试没有使用 Bearer token，也不推断其他平台无需认证。

请求示例：

```http
GET /sbx42843tk/mep1/location/v3/queries/users?address=10.10.0.1 HTTP/1.1
Host: 82.112.253.59
Accept: application/json
```

以下是本次实际列表响应中的第一条 UE，删去其他 UE 和附加字段，保留其值；这是抓取样例，不是固定坐标：

```json
{
  "userList": {
    "user": [
      {
        "address": "10.10.0.1",
        "accessPointId": "4g-macro-cell-6",
        "zoneId": "zone03",
        "timeStamp": {
          "nanoSeconds": 0,
          "seconds": 1794324352
        },
        "locationInfo": {
          "latitude": [
            43.735054
          ],
          "longitude": [
            7.422338
          ],
          "shape": 2
        }
      }
    ]
  }
}
```

RNIS 响应节选：

```json
{
  "cellUserInfo": [
    {
      "ecgi": {
        "cellId": "6060606",
        "plmn": {
          "mcc": "001",
          "mnc": "001"
        }
      },
      "ueInfo": [
        {
          "associateId": [
            {
              "type": 1,
              "value": "10.10.0.1"
            }
          ],
          "erabInfo": [
            {
              "erabId": 243,
              "erabQosParameters": {
                "qci": 80
              }
            }
          ]
        },
        {
          "associateId": [
            {
              "type": 1,
              "value": "10.1.0.1"
            }
          ],
          "erabInfo": [
            {
              "erabId": 2,
              "erabQosParameters": {
                "qci": 80
              }
            }
          ]
        }
      ]
    }
  ],
  "timeStamp": {
    "nanoSeconds": 0,
    "seconds": 1794324352
  }
}
```

Location provider appInstanceId=298b2c0c-7efa-45d3-8b47-8ab3c009b845；serviceId=f8a0d2ab-9977-4d71-8c4f-354b4d59fb32。前者属于服务提供者，不是 RescueTwins consumer 应用 ID。UE 地址与 drone ID 尚未映射，Monaco 经纬度也不能直接当 simulation 网格坐标。回放功能目前不依赖 MEC 网络位置来执行路线。

### 仍未完成的联调

从 Selina 电脑经 Mobius 读取两个 MN 仍超时；需 Zihan 确认当前 IP、网络、防火墙和监听地址。MQTT 密码未提供给运行环境，未向团队服务器做真实上传。独立 drone/MEC AE/ACP、C 区接入、mV 危险阈值、UE/坐标映射、robot 执行反馈、MEC021 自己的应用实例/回调/目标端，以及 shared VM/Kubernetes 实际部署都仍待完成。


非 Windows 环境可在 mec-app/deploy 创建 Python 3.13 虚拟环境，安装 requirements.txt 后运行 `python -m rescuetwin.demo --config config.team.example.json --recorded-only`。要运行原仿真，另安装 requirements-simulation.txt，然后运行项目根目录的 `python simulation/rescue_sim.py --no-anim`；不带该参数需要可用图形桌面。
