# 运行 MEC、Simulation 和 Dashboard

需要 Python 3.13。以下 Windows 命令都在项目根目录执行。

## 给评委看的实时 Swarm（新）

在 repo 根目录的 VS Code PowerShell 运行一个终端：

```powershell
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\start-demo.ps1" -Mode LiveSwarm
```

打开 http://127.0.0.1:8765/#overview。默认80×40网格、8架drone、A/B两区，会自动开始实时探索。不是 JSONL 回放，也不需要 Broker、CSE 或 Hosted Sandbox。想展示6架，追加 `-DroneCount 6`。

- 顶部 Live Swarm Exploration 显示实际 fleet、覆盖率、共享格数、Fusion轮次。
- 选择 Exploration 图层查看共享探索地图；Temperature/Smoke 仍显示示例火情场。
- Run Fire Demo 在实时地图加火；drone 根据各自的局部观测避开已知危险。
- 在 Agent Status 可以查看8架的目标/路线。手动设置目标会暂时覆盖该drone的swarm控制；Resume swarm control 重新启用自主探索。
- 默认是嵌入式 Fusion：每架drone逐轮报告位置及局部观测，Factory只使用合并地图的决策移动。界面明确显示 Embedded Fusion，不冒充已部署到远程MEC。

要演示独立 HTTP Fusion，终端1运行下方 SwarmService，终端2在 repo 根目录运行：

```powershell
$env:PYTHONPATH = "$PWD\mec-app\deploy"
& "$PWD\mec-app\deploy\.venv\Scripts\python.exe" -m rescuetwin.demo --live-swarm --swarm-url http://127.0.0.1:8780
```

服务错误会暂停机群并显示原因，不会静默改用本地地图。

配置在 config.example.json 的 live_factory：width、height、zone_x、cell_m、drone_count、radius、swarm_url；默认80、40、40、2、8、2、空字符串。zone_x 是格坐标，默认物理边界80m，地图160×80m。改布局/注册表会新建Fusion任务，重新计算覆盖率。

共享几何定义在 rescuetwin/layout.py；Fusion按每个任务的map配置和drone_ids校验，不再固定两架或50×30。原 simulation/rescue_sim.py 与其旧回放仍为50×30，两者是明确独立的地图，未重写队友文件。

本轮已验证6/8架实时探索、地图融合、HTTP服务、手动目标、失联暂停、原传感器派单闭环及实际浏览器3D/探索层。实时演示的示例火情/网格运动不是实体无人机飞控或真实MEC021迁移。

## Runtime 清理

已清除14个 pytest/npm/搬迁临时缓存目录。runtime 还包含队列数据库、演示CSE状态和联调报告，并非全部是缓存；这些已保留。测试改用系统临时目录，不重新写入这一批 runtime/pytest-*。

## 1. 启动 MEC 服务（原独立仿真入口）

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
SWARM_MEC_URL=http://127.0.0.1:8780 mec-app/deploy/.venv/bin/python mec-app/deploy/tools/run_swarm.py --quick --no-anim
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


## Swarm logic 实现位置与范围

- simulation/rescue_sim.py：Sariel 的基础模型、局部信息素/蒸发、避障、感知和导出。原文件不改。
- rescuetwin/swarm_service.py：独立 HTTP 服务，合并两架 drone 的局部地图/信息素，处理重复/过期报告。
- rescuetwin/swarm_logic.py：本次补齐的协同控制器；按可探索格的信息增益与路径代价竞价，避免两架 drone 领取相近目标，并预约下一步位置，禁止同格和互换碰撞。目标仍有收益时优先保持，危险变化后重新规划。
- tools/run_swarm.py：把服务分配接到原 Simulation 的适配器。启动器的 Swarm/SwarmHeadless 已使用这个入口；直接运行原 rescue_sim.py 仍是基础算法。

先启动 SwarmService，再启动 Swarm/SwarmHeadless，原有两终端命令不变。HTTP /v1/reports 新增可选 position={x,y}；响应 coordination 包含 assignments、actions、bid、information_gain 和 path。旧地图融合客户端仍兼容；更新后的执行适配器要求新服务，缺少竞价策略会停止，不静默退回本地算法。

信息素代表已探索历史，并按 tick 蒸发；危险图使用归一化仿真值，不能当作 mV 标定阈值。当前策略是探索任务的贪心竞价，不是全局最优求解器，也没有声称完成真实设备控制、CSE切换或 MEC021迁移。

本轮测试：真实 HTTP 服务 + 双 drone 完整运行，seed=1，183步实现100%地图覆盖、发现2个目标，并逐步验证避墙、已知危险格和互撞。此结果只针对该仿真场景，不代表实物性能或多随机种子的性能结论。

需要把输出放到独立目录时，在项目根目录调用 Python 适配器并加 --output-dir（不改变原输入文件）。默认启动器仍使用 simulation 中的两个输出 JSONL，Dashboard 可读取其回放。

本轮验证还包含独立命令行 quick/no-anim 导出至临时目录、认证失败停止执行、重复报告稳定性与非法报告不改变任务状态。部署配置已更新为包含 swarm_logic.py；本次没有完成 Docker/Ubuntu/Kubernetes 实际部署，也未 commit/push。


### 本轮实时验证与清理记录

- 6/8架实时Factory、动态尺寸/边界、独立HTTP Fusion、避碰、手动接管/恢复、Fusion故障暂停，以及原MQTT/CSE派单闭环通过。
- 旧协议/记录/字段兼容回归34项通过；前端模型7项通过；最终LiveSwarm专项6项通过。浏览器确认8架运动、覆盖率增长、3D与探索层、8个控制选项及旧回放。
- LiveSwarm 的本地任务/回执无需 Broker；画面不会把文件读数当作MQTT接收。
- 队友15个原有文件校验值保持不变。未 commit/push；容器入口可导入，未把它说成已完成Docker/VM部署。
- 正常 runtime 清理14个缓存目录；旧重复副本 RescueTwins/mec-app/deploy/runtime 中另有6个受Windows访问权限保护的历史pytest缓存，删除及takeown尝试均被系统拒绝，仍保留。需Windows管理员处理。数据库和联调报告未删。


剩余旧缓存可在管理员 PowerShell 中、repo 根目录运行下面命令。脚本只处理 runtime 下的 pytest-*、npm-cache、relocation-temp，不包含数据库或报告：

```powershell
# Preview cache targets without changing files.
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\tools\clean-runtime.ps1" -WhatIf
# Remove the listed caches.
powershell -ExecutionPolicy Bypass -File "$PWD\mec-app\deploy\tools\clean-runtime.ps1"
```
