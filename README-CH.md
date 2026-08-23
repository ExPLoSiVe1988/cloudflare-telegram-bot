<div align="center"><strong><a href="README.md">فارسی</a></strong> | <strong><a href="README-EN.md">English</a></strong> | <strong><a href="README-CH.md">中文</a></strong></div><br/>

# Cloudflare 管理机器人 🐳

一个功能强大的 Telegram 机器人，用于完整的 DNS 记录管理、智能监控和服务器管理。该机器人配备了三个独立的**监控系统**、**自动故障转移**和**智能负载均衡**，是确保最大正常运行时间和最佳性能的完整解决方案。该机器人与 **Cloudflare**、**ArvanCloud** 和 **Hetzner Cloud** 兼容，提供域名管理、DNS 记录和云服务器功能。

---

<div align="center">
<a href="https://www.youtube.com/watch?v=OOQ9rtHqeFQ" target="_blank">
<img src="https://img.youtube.com/vi/OOQ9rtHqeFQ/hqdefault.jpg" alt="完整教程视频" width="320"></a>
<p><strong>点击上图观看完整教程视频</strong></p>
</div>

## ✨ 功能特性

### 🚀 全面和高级监控

*   👁️ **独立监控**：独立监控任何 IP 或域名（如数据库服务器），当离线时立即收到警报。
*   🎯 **目标通知系统**：
    *   对于**每个规则（故障转移/负载均衡）或每个独立监控**，以**完全独立**的方式定义警报接收者（个人用户或 Telegram 群组）。
    *   警报仅发送给负责的人员和团队，避免垃圾信息和警报疲劳。
*   📍 **集中监控组**：定义可重复使用的监控城市集合（例如"欧洲"、"亚洲"）及其错误阈值，轻松将其分配给任何监控或规则。
*   🛡️ **自动故障转移（高可用性）**：当主服务器故障时，机器人自动将 DNS 记录更改为健康的备用 IP。
*   🚦 **高级加权负载均衡**：
    *   **为 IP 设置权重**：根据服务器容量分配流量（示例：`1.1.1.1:2`）。
    *   **两种智能算法**：在**加权随机**和**加权轮询**（默认）算法之间选择。
*   📊 **高级报告和分析**：
    *   **基于时间的分析**：负载均衡器报告现在显示每个 IP 的**总时间（以小时计）和活跃百分比**。
    *   为自定义时间范围生成监控报告并管理自动日志删除规则。
*   **🏷️ 域名和记录的显示名称（别名）**：为您的域名和记录分配自定义显示名称，便于识别和管理。
*   **👥 从机器人内进行高级用户管理**：
    *   **主管理员**可以直接从机器人内管理**普通管理员**。
*   **📤 记录传输和复制**：轻松在不同域名之间甚至不同的 Cloudflare 账户之间移动 DNS 记录。
*   **🔄 记录类型转换**：即时更改记录类型（例如从 `A` 到 `CNAME`）。
*   **👥 批量操作**：同时删除多条记录或更改其 IP。
*   **💾 备份和恢复**：创建域名记录的 `.json` 备份并恢复。

### 🤖 通用功能和用户体验

*   **📄 域名列表分页**：如果您有很多域名，将不再面对长列表。域名列表（`/list`）现在显示为分页形式。
*   **🚀 快速设置向导**：分步指南，轻松引导新用户创建他们的第一个监控规则。
*   **📊 `/status` 命令**：使用简单命令，实时获取所有规则和监控的在线/离线状态摘要。
*   **🧠 自动数据迁移**：机器人智能检测旧的 `config.json` 文件（包含旧的通知结构）并将其升级到新结构，不丢失用户数据。
*   **🐳 智能和安全的安装脚本**：管理脚本在重新安装期间覆盖设置前会警告您并提供备份 `config.json` 的选项。
*   **🎨 重新设计的用户界面（HTML）**、**多账户支持**和**多语言**。

### ⚙️ 高级 DNS 和用户管理

### 🌐 Cloudflare + ArvanCloud 支持

*   **多提供商 DNS 管理**：通过安装脚本添加和管理 Cloudflare 和 ArvanCloud 账户。
*   **选择 ArvanCloud 域名和记录**：列出 ArvanCloud 域名、查看 DNS 记录并为监控规则选择 `A` 记录。
*   **提供商兼容的故障转移和负载均衡**：先前的 Cloudflare 规则继续工作而不做更改，新的 ArvanCloud 规则可以在服务器故障时自动更改 DNS 记录。
*   **安全升级以前的安装**：您可以通过安装脚本将 ArvanCloud 添加到当前机器人，无需重置以前的设置或规则。

### 🌐 ArvanCloud 功能

| 说明 | 功能 |
|:---|:---|
| 与 Cloudflare 一起管理 ArvanCloud 账户 | **添加和管理账户** |
| 查看 Machine User 有权访问的域名 | **列表域名** |
| 查看 ArvanCloud 域名的 DNS 记录 | **显示 DNS 记录** |
| 为故障转移和负载均衡规则选择 `A` 记录 | **选择 A 记录** |
| 当主服务器故障时，记录 IP 自动更改 | **自动 IP 更改** |
| 保留所有 Cloudflare 规则和设置 | **保留以前的设置** |
| 不重置 `config.json` 而将 ArvanCloud 添加到以前的安装 | **无重置添加** |

### 🌐 获取 ArvanCloud API 密钥和授予域名访问权限

要使用 ArvanCloud，您需要在 ArvanCloud 面板中创建一个 **Machine User** 并授予其对所需域名的访问权限：

1. 登录您的 ArvanCloud 控制面板。
2. 转到账户/IAM 部分并打开 **Machine Users**。
3. 创建一个新的 Machine User。
4. 为创建的 Machine User 生成访问密钥/API 密钥。
5. 在访问部分中，选择您希望机器人管理的域名。
6. 对于选定的域名，启用 DNS 管理访问权限。如果域名未出现在机器人中，也要为相同的 Machine User 启用域名查看/管理访问权限。
7. 通过安装脚本添加创建的密钥，并从 DNS 账户管理部分将其作为 ArvanCloud 账户添加。

> 如果 API 密钥有效但域名未出现在机器人中，通常是因为 Machine User 未被授予域名访问权限或未为这些域名启用 DNS 角色。

### 🟧 Hetzner Cloud 账户管理

> 对于服务器创建/删除、Rescue、Snapshot 和 Primary IP 等管理操作，Hetzner 令牌必须具有读写访问权限。Hetzner Cloud 账户通过安装脚本和服务器账户管理部分添加。

### 🟧 Hetzner Cloud 功能

| 说明 | 功能 |
|:---|:---|
| 查看具有状态、IP 和保护标志的服务器 | **列表服务器** |
| 计划、月度/小时价格、数据中心、位置、镜像、Primary IP 和流量 | **服务器详情** |
| 计算当月的近似成本 | **成本计算** |
| 基于可计费流量（最高 20TB）的流量消费警报 | **流量警报** |
| 开启、重启、关闭和强制关闭 | **服务器控制** |
| 完整系统重置 | **完整重置** |
| 重置 root 用户密码和救援模式 | **重置密码** |
| 选择名称、计划、镜像和位置 | **创建新服务器** |
| 更改服务器名称 | **重命名服务器** |
| 安全确认删除并支持禁用保护 | **删除服务器** |
| 列表、创建、删除、启用/禁用保护并显示卷 | **快照管理** |
| 列表、显示规则、连接和断开连接到服务器 | **防火墙管理** |
| 管理 Primary IP 和 Floating IP，包括创建、分配/断开连接和保护设置 | **IP 管理** |
| 服务器、快照和防火墙的 5 项分页 | **分页** |

---

<div align="center"><h3>💖 支持该项目</h3><p>如果该项目对您有帮助，请通过在 GitHub 上给它一个 star 来支持它！</p><a href="https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/stargazers"><img src="https://img.shields.io/github/stars/ExPLoSiVe1988/cloudflare-telegram-bot?style=for-the-badge&logo=github&color=FFDD00&logoColor=black" alt="在 GitHub 上为项目加星"></a></div>

## 🚀 安装和设置

该机器人设计为使用 Docker 运行。提供的脚本自动化所有设置步骤。

### 自动安装

您可以安装最新的稳定版本或开发版本。对于大多数用户，**安装最新的稳定版本**是推荐的。

```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/main/install.sh)
```

在服务器终端中运行以下命令。请将 `<VERSION>` 替换为 [Releases](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot/releases) 页面上可用的最新版本号（例如 `v4.1.2`）。

```bash
bash <(curl -s https://raw.githubusercontent.com/ExPLoSiVe1988/cloudflare-telegram-bot/<VERSION>/install.sh)
```

该脚本为您提供完整的管理菜单：

*   **安装或重新安装机器人**：下载存储库，允许您选择所需版本（最新或稳定），询问初始设置，并使用 Docker Compose 安装和运行机器人。
*   **从 GitHub 更新机器人**：从 GitHub 获取最新代码。要应用更新，之后您必须再次运行"安装或重新安装机器人"选项。
*   **管理核心配置**：提供通过脚本菜单管理核心设置、账户和管理员的访问权限。
*   **查看实时日志**：显示实时机器人日志以进行监控和调试。
*   **停止机器人/启动机器人**：允许您停止或重启机器人容器而不丢失数据。
*   **完全删除机器人**：停止容器并完全删除所有数据、配置文件、容器和相关镜像。

---

## ⚙️ 配置

所有主要机器人设置都通过安装脚本和管理菜单配置。不需要手动编辑配置文件来添加或修改 Cloudflare、ArvanCloud 和 Hetzner Cloud Manager 账户。

脚本内的重要路径：

```text
管理 DNS 提供商账户
├── Cloudflare
└── ArvanCloud
管理服务器提供商账户
└── Hetzner Cloud
```

---

## 🤖 机器人管理

如果您更喜欢直接使用命令，请导航到项目文件夹（`cloudflare-telegram-bot`）并使用以下 `docker-compose` 命令：

| 命令 | 功能 |
| :--- | :--- |
| `docker-compose logs -f` | **查看实时日志** |
| `docker-compose pull && docker-compose up -d` | **更新到最新版本** |
| `docker-compose down` | **停止并删除容器** |

---

### 所需的 API 令牌权限

对于每个 Cloudflare 账户，您的 API 令牌需要以下权限：

| 类型 | 资源 | 访问权限 |
| :--- | :--- | :--- |
| **Zone** | **DNS** | `Edit` |
| **Zone** | **Zone** | `Read` |

转到 [API 令牌](https://dash.cloudflare.com/profile/api-tokens)页面并创建一个自定义令牌，为 `All zones` 启用这两个权限。

---

### 👨‍💻 开发者和财务支持

*   GitHub: [@ExPLoSiVe1988](https://github.com/ExPLoSiVe1988/cloudflare-telegram-bot)
*   Telegram: [@H_ExPLoSiVe](https://t.me/H_ExPLoSiVe)
*   Telegram 频道: [@Botgineer](https://t.me/Botgineer)

---

### 💖 财务支持（捐赠）

如果该项目对您有帮助，请通过捐赠来支持其开发：

### [支持链接](https://reymit.ir/botgineer)

| 货币类型 | 地址 |
|:---|:---|
| 🔷 **以太坊 (ETH - ERC20)** | `0x157F3Eb423A241ccefb2Ddc120eF152ce4a736eF` |
| 🔴 **波场 (TRX - TRC20)** | `TEdu5VsNNvwjCRJpJJ7zhjXni8Y6W5qAqk` |
| 🟢 **泰达币 (USDT - BEP20)** | `0x78C406B501c4895627CC22F6653AD66163294D60` |

🙏 感谢您的支持！🚀
