# 通过 MQTT 读取 Zone A 温度

读取 CSE 中最新的一条温度记录，不上传或修改数据。需要能连接 Broker，不需要启动 Simulation、Dashboard 或 Sandbox。

## 1. 安装依赖

在项目的 `mec-app/deploy` 目录打开 PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

已有 `.venv` 时跳过第一条。

## 2. 填写连接参数

先复制配置文件，只需做一次：

```powershell
Copy-Item config.mqtt-read.example.json config.mqtt-read.local.json
```

用 VS Code 打开 **`mec-app/deploy/config.mqtt-read.local.json`**，填写：

| 字段 | 填什么 |
|---|---|
| `host` | 当前能访问的 Broker IP 或域名，不带 `http://` |
| `port` | Broker 端口，模板为 `1883`，按实际修改 |
| `username` | 有读取权限的 MQTT 账号 |
| `originator` | 该连接使用的 oneM2M 读取身份，不是 MQTT 账号名 |
| `cse_id` | 目标 CSE ID，模板为 `id-mn-zone-a` |
| `target` | 完整温度路径，模板为 `/id-mn-zone-a/mn-zone-a/node-01/temperature/la` |
| `rvi` | CSE 支持的 oneM2M 版本，模板为 `4` |
| `password_env` | 保持 `MEC_READ_MQTT_PASSWORD` 即可 |
| `timeout_s` | 等待响应的秒数，默认 `10` |

替换所有 `REPLACE_...` 占位内容。确认模板中的 CSE ID 和温度路径与服务器一致。

MQTT 账号需要能发布请求 topic、订阅响应 topic；Originator 需要有目标资源的读取权限。
能上传数据的账号不一定有读取权限。

程序根据 `originator` 和 `cse_id` 自动生成 topic，不用另填：

```text
/oneM2M/req/<originator>/<cse_id>/json
/oneM2M/resp/<originator>/<cse_id>/json
```

这里的 `originator` 使用不含斜杠的身份。如果服务器使用其他 topic 格式，需要调整代码。

## 3. 输入密码并读取

在同一个 PowerShell 终端输入密码，密码不会显示：

```powershell
$brokerPassword = Read-Host 'Broker password' -AsSecureString
$env:MEC_READ_MQTT_PASSWORD = [System.Net.NetworkCredential]::new('', $brokerPassword).Password
```

先检查配置，这一步不会连接 Broker：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin.native_read --config config.mqtt-read.local.json
```

再读取一条记录：

```powershell
.\.venv\Scripts\python.exe -m rescuetwin.native_read --config config.mqtt-read.local.json --read
```

## 4. 查看结果

终端显示结果，同时保存到 **`mec-app/deploy/runtime/mqtt-read-latest.json`**。

- `complete: true`、`rsc: 2000`：读取成功。
- `content`：读取到的数据。
- `cin`：CSE 返回的原始记录。
- `detail`：失败原因。

只检查配置时 `complete` 为 `false`，这是正常的。每次运行会覆盖结果文件。
最新记录可能是旧数据，采样时间要看记录本身，`received_at` 只是本次接收时间。
读取结果目前不会自动显示到 Dashboard。

## Ubuntu 命令

在 `mec-app/deploy` 目录运行，需要 Python 3.13 和 venv：

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.mqtt-read.example.json config.mqtt-read.local.json
```

按上表编辑本地配置后运行：

```bash
read -r -s -p 'Broker password: ' MEC_READ_MQTT_PASSWORD
export MEC_READ_MQTT_PASSWORD
.venv/bin/python -m rescuetwin.native_read --config config.mqtt-read.local.json
.venv/bin/python -m rescuetwin.native_read --config config.mqtt-read.local.json --read
```

已有环境或本地配置时跳过对应创建步骤。

## 常见错误

| 情况 | 检查内容 |
|---|---|
| 连接超时或拒绝连接 | Broker 地址、端口、监听、防火墙、校园网或 VPN |
| 连接未被接受 | MQTT 账号、密码和 TLS 设置 |
| 订阅被拒绝 | 响应 topic 的订阅权限 |
| CSE 响应超时 | 请求 topic 的发布权限、CSE 的 MQTT 连接、响应 topic、`rvi` |
| `rsc=4103` | Originator 的读取权限（ACP） |
| `rsc=4004` | 资源路径是否正确、是否已有温度记录 |

Broker 要求 TLS 时，在本地配置添加 `ca_file` 并修改端口；要求客户端证书时再添加 `cert_file`、`key_file`。

密码不要写进 JSON。本地配置 `config.*.local.json` 和运行结果 `runtime/` 已被 Git 忽略。
