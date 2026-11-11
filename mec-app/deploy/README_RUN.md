# 运行 MEC、Simulation 和 Dashboard

需要 Python 3.13。以下 Windows 命令都在项目根目录执行。

## 1. 启动 MEC 服务

打开第一个终端，运行后保持窗口打开：

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode SwarmService
```

服务地址：`http://127.0.0.1:8780`。

## 2. 运行 Simulation

打开第二个终端：

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Swarm
```

模拟器会连接 MEC 服务、导出记录并显示动画。

- 不显示动画：把 `Swarm` 改成 `SwarmHeadless`。
- 运行 20 轮对照测试：加上 `-Benchmark`，耗时会增加。
- 连接 MEC 失败时会停止，请先确认第一个终端的服务正在运行。

## 3. 打开 Dashboard

等 Simulation 完成数据导出后，在第三个终端运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Recorded
```

浏览器打开：<http://127.0.0.1:8765/#recorded>。

Dashboard 显示记录回放。重新运行 Simulation 后，重启 Dashboard，或在网页中重新上传：

- `simulation/sim_sensor_data.jsonl`
- `simulation/sim_agent_log.jsonl`

首次启动会自动安装需要的依赖。Dashboard 也可以单独启动，查看已有记录。

## 读取 CSE 温度

按 [MQTT 读取说明](docs/mqtt-read-handoff.md) 操作。

Broker 地址、账号和读取身份填写在 `mec-app/deploy/config.mqtt-read.local.json`。
这个文件需要从 `config.mqtt-read.example.json` 复制创建，密码通过环境变量设置。

MQTT 读取是独立命令，读取结果目前不会自动进入 Simulation 或 Dashboard。

## Ubuntu 运行

在项目根目录创建环境并安装依赖，需要已安装 Python 3.13 和 venv：

```bash
python3.13 -m venv mec-app/deploy/.venv
mec-app/deploy/.venv/bin/python -m pip install -r mec-app/deploy/requirements.txt -r mec-app/deploy/requirements-simulation.txt
```

终端 1，进入 `mec-app/deploy` 目录后启动 MEC：

```bash
cd mec-app/deploy
.venv/bin/python -m rescuetwin.swarm_service
```

终端 2，在项目根目录运行 Simulation：

```bash
SWARM_MEC_URL=http://127.0.0.1:8780 mec-app/deploy/.venv/bin/python simulation/rescue_sim.py --quick --no-anim
```

终端 3，在 `mec-app/deploy` 目录启动 Dashboard：

```bash
.venv/bin/python -m rescuetwin.demo --config config.team.example.json --recorded-only
```

## 分开在两台电脑运行

MEC 服务与 Simulation 需要网络互通。在 MEC 主机的 `mec-app/deploy` 目录运行：

```bash
export SWARM_API_TOKEN="替换为自定口令"
.venv/bin/python -m rescuetwin.swarm_service --host 0.0.0.0
```

允许 Simulation 电脑访问 MEC 主机的 8780 端口。在 Windows Simulation 电脑的项目根目录，用 PowerShell 设置：

```powershell
$env:SWARM_MEC_URL = "http://MEC主机的私网IP:8780"
$env:SWARM_API_TOKEN = "与服务端相同的口令"
powershell -ExecutionPolicy Bypass -File .\mec-app\deploy\start-demo.ps1 -Mode Swarm
```

这些 HTTP 命令用于可信内网；通过公网连接时配置 HTTPS。
容器部署使用 `compose.swarm.yaml`，Kubernetes 配置见 `k8s/swarm.yaml`；两者都需要填写实际部署参数。

当前地图为 50×30，只有 A/B 两区和 drone-A/B。node-01 使用历史传感器记录，node-02 使用模拟数据。
本机运行已验证，Ubuntu VM 部署仍需实测。
