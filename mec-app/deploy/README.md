# RescueTwin：MQTT、MEC-IPE 与 Sandbox 集成

这是 RescueTwins 团队的 MQTT 与 MEC 集成模块，位于 `mec-app/deploy/`。
实现保持你们约定的 8 个 sensor 字段，并兼容队友的 HTTP CIN 接口。
开发记录日期：2026-10-15；feature freeze：2026-11-08。

## 数据路径

```text
ESP32 / simulation
  └─ MQTT QoS 1：rescuetwin/{zone}/{device}/{sensor}
       └─ bridge → SQLite durable outbox → HTTP(S) oneM2M CIN → MN-CSE A / B

ETSI MEC013 Location ─┐
ETSI MEC012 RNIS ─────┴─ MEC-IPE → oneM2M CIN
oneM2M requests CIN → MEC-IPE → MEC REST query → oneM2M responses CIN

agent position → factory geofence → prepare target AE → target position CIN
              → handover event CIN → persist new serving zone
```

另提供 `native-publish`：按 TS-0010 的原生 oneM2M MQTT binding 发送 request primitive。
它与“普通 JSON MQTT + HTTP bridge”是两个可选路径。本地测试验证了两者；
部署模板使用 bridge 路径，便于与当前队友的 HTTP 接口集成。

## 先跑本地测试

需要 Python 3.13。从团队仓库根目录先进入本模块，再执行后续命令：

```powershell
cd mec-app/deploy
```

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest -q
```

测试会临时启动真实 MQTT broker、两个 ACME MN-CSE 和一个 ACME IN-CSE，自动结束它们。
位置和 RNIS 使用符合接口结构的本地测试服务；它们不是 ETSI 在线服务。
TLS 测试使用临时私有 CA，测试证书不进入交付文件。

## 本地持续运行

先启动 Docker Desktop 的 Linux engine，再运行：

```powershell
docker compose -f compose.local.yaml up -d --build
.\.venv\Scripts\python.exe -m rescuetwin provision
```

在第二个终端运行：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin bridge
```

在第三个终端发布模拟读数：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin publish sample-temperature.json --now
.\.venv\Scripts\python.exe -m rescuetwin status
```

`status` 应出现 `delivered`。MQTT PUBACK 只证明 broker 已接收；CSE 的最新记录才证明数据已写入。
读回位置为 `<A_BASE_URL>/node-01/temperature/la`，header 使用 `X-M2M-Origin: CNode01`、
新的 `X-M2M-RI`、`X-M2M-RVI: 4` 和 `Accept: application/json`。

本地 compose 把无加密端口绑定到 `127.0.0.1`。ESP32 无法直接访问这个 loopback 地址。
硬件联调使用下述 TLS 部署的可达地址；由硬件队友把各节点自己的 client certificate 和 key 写入固件。

## MQTT 与硬件约定

示例 topic：`rescuetwin/A/node-01/temperature`，QoS `1`，retain `false`。

```json
{
  "device_id": "node-01",
  "zone_id": "A",
  "sensor_type": "temperature",
  "value": 28.6,
  "unit": "degC",
  "source": "physical",
  "quality": "valid",
  "observed_at": "2026-10-15T08:30:00Z"
}
```

| sensor_type | unit | value 类型 |
|---|---|---|
| temperature | degC | 有限数字 |
| humidity | percent | 0..100 |
| pressure | hPa | 正数 |
| smoke_raw / co_raw | adc | 0..4095 整数（ESP32 12 bit ADC） |
| presence | boolean | JSON `true` / `false` |

`zone_id` 为 A/B，`source` 为 physical/simulation，`quality` 为 valid/invalid。
失效传感器可发 `quality: invalid, value: null`；边缘决策应排除 invalid 数据。
数值范围检查不是火灾或 CO 浓度判断，`adc` 不代表 ppm。
字段名、topic 中的设备及区域、配置中的设备映射必须一致；额外字段也会被拒绝。

每条读数转成：

```json
{"m2m:cin":{"rn":"cin-<稳定记录哈希>","cnf":"application/json:0","con":"<八字段JSON字符串>"}}
```

POST 路径为 `<BASE_URL>/<device_id>/<sensor_type>`；使用 `application/json;ty=4`。
Originator 从配置读取，node-01 默认 `CNode01`。每次 HTTP 请求重新生成 `X-M2M-RI`。
必须核对实际注册返回的 `aei`：不能仅凭 AE 名推断它。
`provision` 遇到不同 AE identity 会停止，不替换已有资源或队友的 ACP。

## 接入 public try 与 ESTIMED shared

1. 在 [public MEC Sandbox](https://try-mec.etsi.org/) 登录并选择网络场景。
2. 在 API console 分别选择 Location 和 RNIS，复制 **Try-it in your MEC Application** 的 URL。
   URL 要包含你的 sandbox instance 和对应 MEC platform。不要从首页 URL 猜 API 地址。
3. 把 `config.example.json` 复制为 `config.local.json`，填写 `mec.location_url` 和 `mec.rnis_url`。
   这里填写 API base，代码会追加 `/queries/users` 或 `/queries/rab_info`。
4. 用 Location 查询得到的实际 `address` 更新 `agents` 映射。默认 `10.10.0.1` 只适用于我们的自定义场景。
   工厂原点也必须与场景坐标一致；未映射用户不会影响 handover。
5. 如需 MEC011 READY，填写 `app_support_url`，用主办方或 Sandbox API 创建的 app instance ID 调用：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json confirm-ready YOUR_APP_INSTANCE_ID
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json ipe
```

public try 能提供模拟 MEC API；登录或创建 app instance ID 都不等于把 Docker 镜像部署到了真实 MEC host。
shared 环境的 SSH/Kubernetes 权限、节点名称、image registry 和 MEC service URLs 仍须由主办方提供。
MEC011 生命周期中的 termination subscription 和真实 MEC021 application-state transfer 也须进一步联调。

## 自定义工厂与网络测试

`scenario/factory.yaml` 是 AdvantEDGE 1.11 场景：100m × 60m 工厂、A/B 两区、
两台逻辑 EDGE host、10 个移动 UE 和一个 cloud DC。
`scenario/factory-map.geojson` 给 twin 使用。只有配置中显式映射的 agents 会进入 MEC-IPE，其他 UE 是场景演示设备。
factory boundary 为 x=50m；默认 2m 缓冲，并且连续两个新位置确认后才切换。
过期、重复时间戳、无坐标或工厂外的位置不会触发 handover。

重新生成场景：

```powershell
.\.venv\Scripts\python.exe tools/scenario.py
```

对有 Sandbox Control 权限的本地/shared 环境：

```powershell
.\.venv\Scripts\python.exe tools/sandbox.py --base-url https://YOUR_HOST/YOUR_SANDBOX/sandbox-ctrl/v1 import scenario/factory.yaml
.\.venv\Scripts\python.exe tools/sandbox.py --base-url https://YOUR_HOST/YOUR_SANDBOX/sandbox-ctrl/v1 activate rescuetwin-factory
.\.venv\Scripts\python.exe tools/sandbox.py --base-url https://YOUR_HOST/YOUR_SANDBOX/sandbox-ctrl/v1 network in-host --type DC --latency-ms 80 --jitter-ms 10 --loss-percent 2
```

恢复测试网络时把三个参数都设为 0。先保存原配置，若原值不为 0 则恢复原值。
`packetLoss` 的单位是百分比，2 表示 2%。必须确认实际应用流量穿过模拟网络，
否则场景中的 impairment 不会作用于直接访问 localhost 或外部 URL 的请求。
测试建议：0/20/80/150ms 延迟、0/1/5% 丢包，分别测试 edge 和 IN 路径，记录成功率与 P50/P95。

## 双向 MEC ↔ oneM2M

MEC → oneM2M：IPE 轮询 Location / RNIS，把原响应写入 `mec-ipe/location` 和 `mec-ipe/rnis`。
oneM2M → MEC：创建 `mec-ipe/requests` CIN，IPE 查询相应服务并写 `mec-ipe/responses`。

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json command get_location --address 10.10.0.1
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json command get_rnis
```

每个请求有 `command_id`。IPE 发现所有保留的 request CIN，不只读取 `/la`，因此支持先排队多个请求。
用 `command_id` 关联 responses。当前允许 Location/RNIS 查询；这展示了双向数据调用。
handover 使用应用层 re-registration，并保留旧 CSE 的历史；它未实现 MEC021 context migration、订阅迁移、旧注册删除或机器人控制指令迁移。

## 延迟测量

```powershell
.\.venv\Scripts\python.exe -m rescuetwin --config config.local.json benchmark --count 100 --output runtime/storage-rtt.csv
```

未配置 `VIA_IN` 时，此命令比较 A 和 IN 的直接 CIN 创建+读回往返时间。
完成 MN↔IN 注册后，可以加：

```json
"VIA_IN": {
  "base_url": "http://IN_HOST:18000/~/id-mn-a/cse-mn-a",
  "originator": "/id-mn-a/CBenchmark"
}
```

这会比较直达 MN-A 与经 IN-CSE 转发到同一个 MN-A 的相同工作负载。
shared 使用 HTTPS URL、对应 CA 和认证；TLS Mcc 配置由 CSE 队友提供。
本地测试已验证 ACME↔ACME 的 Mcc 转发；ACME↔Mobius 的互操作仍需实测。
`tools/acme_config.py` 默认不启用远端注册，设置 `CSE_REMOTE=true`、正确的 `REGISTRAR_HOST`/`REGISTRAR_PORT`
可用于本地无加密 federation 测试。不要把该测试设置直接用于 shared。
存储 RTT 不是“ESP32 读数→MEC 决策→agent reroute”延迟。
最终应用指标需硬件/算法队友提供 observation 与 reroute 的同一事件标识和时间测量点。

## 两台 MEC host 上的 TLS 部署

`k8s/template.yaml` 是待填写 image 地址的模板；A/B 的 nodeSelector 分别使用
`rescuetwin-mec-zone=A/B`，bridge、MEC-IPE 和 broker 放在 A。
`CSE_REMOTE` 默认 false，模板中的 IN 是 ACME 验证实例。由 Zihan 提供 Mobius 配置后再替换 IN 并开启 Mcc。
本目录没有 swarm 算法；MEC-IPE 与 bridge 是本次可部署的 edge integration applications。

构建并推到主办方允许的 registry，再生成：

```powershell
docker build -f Dockerfile -t YOUR_REGISTRY/rescuetwin:2026-11-08 .
docker push YOUR_REGISTRY/rescuetwin:2026-11-08
.\.venv\Scripts\python.exe tools/k8s.py --image YOUR_REGISTRY/rescuetwin:2026-11-08 --config config.production.example.json
```

先填写 production config 的 MEC URLs 和 agents 映射。运行 `tools/certificates.py --output certs --hostname ACTUAL_HOSTNAME`
可生成演示 CA、server certificates、独立节点 client certificates 和随机 CSE tokens。
CA key 留在离线开发机；node-01 只领取 `ca.crt`、`node-01.crt` 和 `node-01.key`。
HTTPS 校验 CA 与 hostname；MQTT 使用 mTLS，并按证书 CN 配置 ACL。
HTTP 使用 bearer token 加现有 oneM2M ACP。token 仅授予可信 bridge/IPE 或 CSE 运维端，ESP32 使用自身 MQTT 身份。
生产环境使用主办方/团队管理的证书与认证配置。

主办方确认两台节点后，operator 执行：

```powershell
kubectl create namespace rescuetwin
kubectl label node ACTUAL_NODE_A rescuetwin-mec-zone=A
kubectl label node ACTUAL_NODE_B rescuetwin-mec-zone=B
kubectl -n rescuetwin create secret generic cse-tokens --from-file=a=certs/a.token --from-file=b=certs/b.token --from-file=in=certs/in.token
kubectl -n rescuetwin create secret generic cse-a-certs --from-file=tls.crt=certs/cse-a.crt --from-file=tls.key=certs/cse-a.key
kubectl -n rescuetwin create secret generic cse-b-certs --from-file=tls.crt=certs/cse-b.crt --from-file=tls.key=certs/cse-b.key
kubectl -n rescuetwin create secret generic cse-in-certs --from-file=tls.crt=certs/cse-in.crt --from-file=tls.key=certs/cse-in.key
kubectl -n rescuetwin create secret generic bridge-certs --from-file=ca.crt=certs/ca.crt --from-file=bridge.crt=certs/bridge.crt --from-file=bridge.key=certs/bridge.key
kubectl -n rescuetwin create secret generic mqtt-certs --from-file=ca.crt=certs/ca.crt --from-file=broker.crt=certs/broker.crt --from-file=broker.key=certs/broker.key
kubectl apply -f k8s/template.yaml
kubectl -n rescuetwin rollout status deployment/cse-a
kubectl -n rescuetwin rollout status deployment/cse-b
kubectl -n rescuetwin exec deployment/bridge -- python -m rescuetwin --config /config/config.json provision
kubectl -n rescuetwin get pods -o wide
```

若 MEC service 需要 token，另用本地 token 文件创建 `mec-access` secret 的 `token` key。
broker Service 默认 ClusterIP。让硬件访问前，需主办方提供合适的 LoadBalancer/NodePort/私网路径，
并把其实际 DNS 名写入 broker certificate SAN；不要猜公网端口。
部署验收要保存 pods placement、HTTPS 读回、设备 mTLS、Location/RNIS 原响应和 handover 证据。
不能仅凭生成 YAML 就把“已部署”打勾。

## 团队协作

源码按以下结构维护：

```text
mec-app/deploy/
  rescuetwin/        # MQTT bridge、oneM2M client、MEC-IPE、命令行
  broker/            # Mosquitto 配置和按设备限制的 ACL
  k8s/               # 两个 MEC host 的部署模板
  scenario/          # 工厂地图和 UE 轨迹
  tools/             # CSE 配置、证书、场景和部署生成工具
  tests/             # 真实 MQTT/ACME 和 TLS 集成测试
  docs/              # 状态表、文章草稿和接口联调记录
  Dockerfile
  compose.local.yaml
```

本模块是可独立运行的集成原型；团队的 ACME/Mobius 正式配置仍由 `cse-config/` 维护者提供。
使用功能分支提交变更，并通过 PR 合并到 `main`。队友的拉取与运行步骤见 [团队协作指南](docs/team-collaboration.md)。
官方 ETSI Sandbox 作为外部依赖保留在原来的 clone，来源为 `https://labs.etsi.org/rep/mec/etsi-mec-sandbox.git`。
本模块不依赖该 clone 的本地绝对路径；场景运行需可用的 AdvantEDGE/MEC Sandbox 服务。

## 官方资料

- [oneM2M TR-0077：deployment options](https://specifications.onem2m.org/tr/tr-0077/latest/5.1/)
- [TR-0077：handover scenarios and gaps](https://specifications.onem2m.org/tr/tr-0077/latest/7.2/)
- [ESTIMED developer materials](https://estimed.etsi.org/dev_material/)
- [ESTIMED reports and specifications](https://estimed.etsi.org/reports_specifications/)
- [TS-0010：MQTT primitive mapping](https://specifications.onem2m.org/ts/ts-0010/latest/6.5/)
- [ACME HTTP configuration](https://acmecse.net/setup/Configuration-http/)
- [ACME MQTT configuration](https://acmecse.net/setup/Configuration-mqtt/)

已通过登录后的 ETSI Labs 确认：
[TR-0086：Developer Guide oneM2M and MEC deployment](https://labs.etsi.org/rep/estimed/wp6/tr-0086/-/blob/50473eb37a47a3307097c543c8618be1947a171f/TR-0086-Developer_Guide_oneM2M_and_MEC_deployment.md)
确实存在。该版本标为 v0.0.0，日期为 2026-06-05，仍含草稿内容。
优先按 §5.2 启动并注册平台、§5.3 接入设备、§5.4 部署 MEC application，再验证 §5.5 handover。
官方资料入口与真实环境接入步骤见 [Labs 接入后的下一步](docs/next-steps-after-labs-access.md)。
