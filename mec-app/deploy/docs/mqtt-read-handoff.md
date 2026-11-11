# Zone A：通过 MQTT 读取一条最新温度记录

此入口只发 oneM2M RETRIEVE（op=2），不注册 AE、不创建或修改 CSE 资源。
无需 Sandbox、Docker、Dashboard 或 NumPy。先在能访问校园 Broker 的队友电脑运行；
以后把相同代码和配置放到 Ubuntu VM 再测。此测试成功不代表已经完成 MEC VM 部署。

## 需要队友确认的信息

- 当前可达的 Broker 主机、端口、TLS/CA 要求。
- 获准读取的 MQTT username、密码，以及对应的 oneM2M Originator。
- 请求 topic 的发布权限、响应 topic 的订阅权限，以及目标温度 CIN 的读取 ACP。
- 实际 CSE ID、完整温度资源路径和支持的 rvi。

模板路径为 `/id-mn-zone-a/mn-zone-a/node-01/temperature/la`，需要 CSE 负责人确认。
模板不默认借用 node-01 上传账号；该账号能上传不等于获准读取。
此命令使用 `/oneM2M/req/<originator>/<cse_id>/json` 和
`/oneM2M/resp/<originator>/<cse_id>/json`；如实际部署采用不同 topic 约定，需要先调整适配。

## Windows PowerShell

在项目的 `mec-app/deploy` 目录运行。项目现有环境使用 Python 3.13：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.mqtt-read.example.json config.mqtt-read.local.json
```

编辑 `config.mqtt-read.local.json`，替换所有 REPLACE 字段，确认 target、port、cse_id、rvi。
密码放环境变量，不写入配置。可通过安全输入设置：

```powershell
$readCredential = Get-Credential -Message '输入 Broker 账号和密码（账号还需与配置一致）'
$env:MEC_READ_MQTT_PASSWORD = $readCredential.GetNetworkCredential().Password
```

先检查配置（不连接网络），再执行一次读取：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin.native_read --config config.mqtt-read.local.json
.\.venv\Scripts\python.exe -m rescuetwin.native_read --config config.mqtt-read.local.json --read
```

## Ubuntu

在同一目录运行，需要 Python 3.13 及其 venv 支持：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.mqtt-read.example.json config.mqtt-read.local.json
# 编辑本地配置后，私下输入 Broker 密码
read -r -s -p 'Broker password: ' MEC_READ_MQTT_PASSWORD
export MEC_READ_MQTT_PASSWORD
.venv/bin/python -m rescuetwin.native_read --config config.mqtt-read.local.json
.venv/bin/python -m rescuetwin.native_read --config config.mqtt-read.local.json --read
```

## 成功条件和排错

输出保存在 `runtime/mqtt-read-latest.json`。只有收到匹配 rqi、rsc=2000 且包含
`m2m:cin.con` 的响应才标记 `complete=true`。`content` 是解析后的记录，`cin` 保留原始
CIN 内容。`received_at` 是本次接收时间，不代表传感器采样时间；latest 记录也可能是旧记录。
报告内容格式不强制改写，不把成功读回等同于满足八字段数据约定或真实硬件验收。
失败退出码为 1，默认预览 complete=false；每次运行覆盖报告，避免误用上次成功结果。

- 连接超时/拒绝：先检查 Broker 地址、监听、防火墙、校园内网或 VPN 路由。
- 连接未获接受：检查 MQTT 账号密码及 TLS。
- 订阅被拒绝：检查响应 topic ACL。
- CSE 响应超时：检查请求 topic ACL、CSE 是否连接此 Broker、响应路由和 rvi。
- rsc=4103：检查读取身份和 ACP；rsc=4004：检查路径及是否已有温度记录。

TLS Broker 在本地配置添加 `ca_file`（受信任 CA 文件），如要求双向认证再添加
`cert_file`、`key_file`，并填写实际 TLS 端口；保持证书校验开启。

将运行报告交回团队即可定位下一步；报告包含传感器数据，分享前确认范围。
不要提交密码、私钥或本地配置。代码包至少包含 rescuetwin/、requirements.txt、
config.mqtt-read.example.json 和本说明，不要打包 .venv/ 或 runtime/。
