# Drone + independent MEC swarm app (50x30, A/B)

队友在校园网通过 MQTT 读取 Zone A 最新温度：见 [读取入口与交接步骤](docs/mqtt-read-handoff.md)。
该入口独立运行，不需要启动下面的无人机模拟或 Dashboard。

当前版本：50 列 × 30 行；A 区 x=0..24，B 区 x=25..49。每格尚未标定米数。
取消 C 区和 drone-C，保留 drone-A/B。node-01 为历史物理读数回放，node-02 为模拟读数。
旧三分区日志不兼容，请重新生成；不会把 C 的历史数据偷偷改标成 B。

## 在自己电脑运行（CMD）

先 cd 到完整项目根目录。需要 Python 3.13。每个命令放在单独终端。

终端 1：启动独立 MEC 地图合并服务，保持窗口打开：

```bat
powershell -ExecutionPolicy Bypass -File ".\mec-app\deploy\start-demo.ps1" -Mode SwarmService
```

终端 2：运行 drone 模拟和二维动画：

```bat
powershell -ExecutionPolicy Bypass -File ".\mec-app\deploy\start-demo.ps1" -Mode Swarm
```

只生成回放、不弹动画：将 Swarm 换成 SwarmHeadless。默认跳过耗时的 20 轮对照基准；需要基准时加 -Benchmark。
模拟器默认连接 http://127.0.0.1:8780；连接/鉴权失败会停止，不会悄悄切回本地地图。

终端 3：完成导出后打开 Dashboard：

```bat
powershell -ExecutionPolicy Bypass -File ".\mec-app\deploy\start-demo.ps1" -Mode Recorded
```

浏览器打开 http://127.0.0.1:8765/#recorded 。网页读取刚生成的 JSONL；它是回放，不是实时控制端。
已有网页需要重新上传 simulation/sim_sensor_data.jsonl 和 sim_agent_log.jsonl，或重启网页服务。
Recorded 模式可独立运行；LocalDemo/LocalCse 是另一套旧工厂示例，不用于验证本次 MEC swarm 链路。

## 在 MEC host 运行

MEC swarm 服务可以单独部署，完全不依赖 Dashboard、NumPy、MQTT 或 Sandbox。
在可管理的 MEC host 上，使用 Dockerfile.swarm 构建并运行 compose.swarm.yaml：

```sh
export SWARM_API_TOKEN="替换为团队生成的随机口令"
export SWARM_BIND_IP="MEC主机的私网IP"
docker compose -f compose.swarm.yaml up --build -d
```

上面命令在 mec-app/deploy 目录执行。私网 IP 必须属于该主机；默认只绑定 127.0.0.1。
允许 drone 机器访问私网端口 8780。跨不可信网络时使用 HTTPS 反向代理，并让客户端使用其 HTTPS URL。
Kubernetes 使用 k8s/swarm.yaml：填写实际镜像，创建 rescuetwin 命名空间及 swarm-access Secret 的 token 键，
给目标节点设置 rescuetwin-mec-zone=A 标签后部署。服务只有一个副本；不要横向扩容未共享的内存状态。
ClusterIP 供集群内部访问；集群外客户端还需要实际可达的私网入口或 HTTPS Ingress。

队友在自己的 CMD 中设置，然后运行 Swarm/SwarmHeadless：

```bat
set SWARM_MEC_URL=http://MEC主机私网IP:8780
set SWARM_API_TOKEN=与服务端相同的口令
powershell -ExecutionPolicy Bypass -File ".\mec-app\deploy\start-demo.ps1" -Mode Swarm
```

不要把口令提交到仓库。HTTPS 仍校验证书；客户端不会关闭验证。
本次完成了本机独立进程联调和部署文件，尚未在真实 MEC host 上部署验收。
Hosted Sandbox 提供的 Location API 是另一个服务；连接它不等于你拥有该主机的部署权限。

## 数据接口与计算

GET /health 返回服务、50x30 网格及 A/B 分区。
POST /v1/reports 需 JSON；非本机部署要求 SWARM_API_TOKEN，使用 Authorization: Bearer <token>。

```json
{"mission_id":"run-unique-id","drone_id":"drone-A","sequence":0,"tick":0,"cells":[{"x":1,"y":1,"hazard":0.8}]}
```

每个 drone 上报当前感知范围内全部有效格子的危险值（0..1，含安全格的 0）。
mission_id 隔离不同运行；同一运行的两架 drone 使用同一个 ID 和共享步时钟。
每个 drone 的 sequence 递增；相同序号同内容可重试，冲突及倒序报告拒绝。
服务合并所有 drone 的已探索格子；pheromone 按最后观察步数以 0.997^age 衰减。
危险值在有效的最新局部报告间取最大值，大于 0.6 标为 blocked。
报告超过 8 个模拟步或 30 秒未刷新即不参与危险区计算；新报告的 0 值可清除该 drone 的旧告警。
失去联系后的未知格不等于物理安全；这里仍是简化任务模型。
返回 pheromone、hazards、active_drones 及地图版本尺寸，drone 用合并结果选下一步。
服务不控制真实飞控，也没有高度、起降或物理飞行动力学；mV 到危险值的归一化仍属于示意模型。

状态暂存在内存；重启丢失合并地图，客户端需新建 mission 重跑。无活动任务一小时后清除，最多 32 个任务。
源 simulation 可以不设置 SWARM_MEC_URL 直接运行纯本地算法对照；统一启动器的 Swarm 模式始终使用独立服务。

## 检查

在 mec-app/deploy 目录：

```bat
.venv\Scripts\python.exe -m pytest -q tests/test_swarm_service.py tests/test_recordings.py tests/test_links.py
```

涵盖独立服务进程、真实 HTTP 上报/决策、合并/衰减/过期、乱序/重复、权限失败、50x30 地图一致性及 A/B 回放。
