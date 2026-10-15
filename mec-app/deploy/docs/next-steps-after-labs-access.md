# 获得 ETSI Labs 项目访问后的下一步

核对日期：2026-10-15。三处项目均通过登录后的浏览器读取；团队集成代码现位于 `RescueTwins/mec-app/deploy/`。

## 三个项目各自的作用

| 项目 | 已确认内容 | 你应如何使用 |
|---|---|---|
| `estimed/estimed.etsi.org` | ESTIMED 官网源码，含 content、themes、config.toml 等 | 查公开开发资料和资源入口，无需把你们的实现放进去 |
| `estimed/wp6/hackathons/hackathon-2` | tutorials、MEC/oneM2M code skeletons、示例和成果提交说明 | 克隆作参考，优先读官方集成示例 |
| `estimed/wp6/tr-0086` | Developer Guide oneM2M and MEC deployment，草稿 v0.0.0 | 按平台启动、设备接入、MEC app 注册和 handover 的顺序核对系统 |

项目成员权限证明能读取代码和文档；还不能证明已有 VM、Kubernetes 或自定义镜像部署权限。

## 先统一团队代码

团队继续使用 `Riel-3288/RescueTwins`。自写集成代码已从本地
`etsi-mec-sandbox/examples/rescuetwin/` 整理到本模块。
官方 sandbox 保留为上游依赖，用 README 记录来源、版本和启动步骤。

团队 README 把 `mec-app/deploy/` 分配给 Selina。本次迁移已确认原路径为空文件，并将其转换为目录。

协作流程：

1. 获得团队仓库写入权限并克隆 `RescueTwins`。
2. 创建自己的功能分支，例如 `feature/mqtt-mec-integration`。
3. 添加集成代码、依赖、配置示例和 build/deploy 文档。
4. 本地运行集成测试，检查提交内容。
5. commit 后 push 到团队仓库的该分支，再开 pull request。
6. 团队合并后，各队友 pull 更新。

commit 只记录在本地；队友要拿到代码，还需要 push。已有团队仓库时通常无需另建一个 sandbox GitHub 仓库。
如果确实修改了 sandbox 平台本身的源代码，再把平台补丁单独维护为 fork 或独立分支。

提交源码和示例配置。证书私钥、tokens、真实环境配置、虚拟环境、数据库及 ETSI 受限资料保留在各自原来的安全位置。

## 按小链路完成真实联调

1. 核实当前环境的 IN-CSE、MN-CSE A、MEC011 地址和认证资料；按 TR-0086 §5.2 验证 MN→IN 的 remoteCSE 注册。
2. 用一个模拟 node-01 验证 MQTT→MN-CSE A→读回，再接真实 ESP32。
3. 用一个真实 Sandbox UE 验证 MEC013 Location→MEC-IPE→CSE 的位置记录。
4. 跑通第二个 MEC host/MN-CSE B，再验证一次 A→B handover；检查 AE-ID、遥测和订阅连续性。
5. 加入 RNIS 或自定义非位置服务、双向请求与故障/延迟测试。

官方 MEC skeleton README 描述：`4g-5g-mc-v2x-fed-iot` 场景会包含 cloud IN-CSE 与 mep1 MN-CSE。
因此应先在自己获分配的 Sandbox 确认该场景及其端点可用；若能复用，可先完成服务级联调。
这仍不等于已经部署了自己的 CSE 镜像或 swarm app。

优先参考：Demo 1（应用基本流程）、Demo 6（MEC assisted oneM2M handover）、Demo 7（自定义服务）、Demo 8（ESP32/Wokwi）。
Demo 编号来自 Hackathon 仓库；它们与 `etsi-mec-sandbox` 仓库里的同编号示例不一定相同。
索引中有部分过期路径，例如 Demo 6 的 `src/README.md` 链接失效，应从实际 demo6 目录进入。

## 成果提交

Hackathon-2 当前 master README 写明用 Hackster 项目发布团队成果，并允许代码托管在 GitHub 或 GitLab。
队内开发继续在 RescueTwins，最终把仓库、架构、运行步骤、演示和测试证据整理进 Hackster。
README 中的 tag 是例子，最终项目标签与截止要求以主办方的最新通知为准。

## 资料链接

- [Hackathon README 与提交说明](https://labs.etsi.org/rep/estimed/wp6/hackathons/hackathon-2/-/blob/master/README.md)
- [MEC skeleton 说明](https://labs.etsi.org/rep/estimed/wp6/hackathons/hackathon-2/-/blob/master/examples/MEC-code-skeletons/README.md)
- [Demo 6 实际目录](https://labs.etsi.org/rep/estimed/wp6/hackathons/hackathon-2/-/tree/master/examples/MEC-code-skeletons/demo6)
- [TR-0086 Developer Guide](https://labs.etsi.org/rep/estimed/wp6/tr-0086/-/blob/50473eb37a47a3307097c543c8618be1947a171f/TR-0086-Developer_Guide_oneM2M_and_MEC_deployment.md)
