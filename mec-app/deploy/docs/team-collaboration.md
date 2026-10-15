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

持续运行本地 broker 与三个演示 CSE 时，启动 Docker Desktop 的 Linux engine，再按
[模块 README](../README.md) 的 Compose、provision、bridge、publish 顺序操作。
新目录中的 `.gitignore` 和 `.dockerignore` 排除了虚拟环境、运行数据、私钥、tokens 和本地配置。

## 交给队友的接口

| 部分 | 所需信息 | 当前接口 |
|---|---|---|
| 硬件 | Broker 地址、每台设备自己的证书、zone/device/sensor 映射 | MQTT topic `rescuetwin/{zone}/{device}/{sensor}`，QoS 1、retain false；payload 为约定的八字段 JSON |
| CSE | A/B/IN 的 base URL、实际注册 aei、认证与 ACP | Bridge POST 到 `<BASE_URL>/<device>/<sensor>`，创建 `m2m:cin`，`con` 为读数 JSON 字符串 |
| Swarm / twin | CSE 资源路径、事件 schema、更新或订阅方式 | IPE 的 `mec-ipe/location`、`rnis`、`requests`、`responses`、`handover`；agent 的 `position` |
| MEC | 用户自己的 API base、UE address、应用 instance ID | MEC013 Location、MEC012 RNIS、MEC011 confirm_ready |

CSE 正式配置继续由 `cse-config/` 的维护者负责。本模块的 `tools/acme_config.py` 只提供独立测试/部署示例。
先用一个模拟 node-01 验证 MQTT→MN-A→读回，再接真实设备和 MEC 位置，最后联调 MN-B 与 handover。

## 当前验证边界

测试包含真实 MQTT 与 ACME CSE、Mcc 转发、HTTPS 和 MQTT client certificate 验证。
MEC REST 输入来自本地测试服务。真实 ESTIMED 部署、Mosquitto ACL、物理 ESP32、ACME–Mobius
互操作、MEC021 context transfer 和完整订阅迁移仍需各自环境的验收证据。
