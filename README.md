<p align="center">
  <img src="docs/assets/logo.svg" width="88" height="88" alt="NEU Court Booking 像素风羽毛球项目标志">
</p>

<h1 align="center">NEU Court Booking</h1>

<p align="center">
  东北大学南湖羽毛球预约助手<br>
  <sub>中文桌面界面 · 单日预约 · 本人登录与付款</sub>
</p>

<p align="center">
  <a href="https://github.com/Oppie2333/neu-court-booking/releases/tag/v1.0.0"><img src="https://img.shields.io/badge/version-1.0.0%20preview-217c74?style=flat-square" alt="1.0.0 预发布"></a>
  <img src="https://img.shields.io/badge/Windows-10%20%2F%2011%20x64-245a96?style=flat-square" alt="Windows 10/11 x64">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-596675?style=flat-square" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/requires-Chrome-596675?style=flat-square" alt="需要 Chrome">
</p>

<p align="center">
  <a href="https://github.com/Oppie2333/neu-court-booking/releases/tag/v1.0.0">下载运行包</a> ·
  <a href="docs/使用说明.md">使用说明</a> ·
  <a href="NEWS.md">NEWS</a> ·
  <a href="CHANGELOG.md">更新记录</a> ·
  <a href="https://github.com/Oppie2333/neu-court-booking/issues">问题反馈</a>
</p>

<p align="center">
  <img src="docs/assets/neu-nanhu-pixel.png" width="800" alt="像素 Q 版东北大学南湖校门与羽毛球运动插画：石门柱、中央教学楼、球场和跃起扣杀的运动员。">
</p>

面向拥有学校预约权限的同学，帮助完成**一次日期明确、最多一份订单**的南湖羽毛球预约。设置日期与时段后，由本人登录学校，程序等待放号并处理预约流程。

📍 **适用范围**：目前程序主要用于东北大学**南湖羽毛球馆**预约，开发和测试也以南湖馆为主。南湖馆仅有 **12 片场地**，热门时段较难预约；根据维护者近期体验，浑南暂不存在同样的场地紧张问题。本项目尚未针对浑南馆完成适配验证。

> **当前为 v1.0.0 测试版。** 已完成本机自动测试与打包启动检查，真实付费第二层页面仍待完整验证。学校规则和网页可能变化，不保证高峰期抢到场地。本项目由个人维护，非学校官方产品。

## 🚀 三步开始

| 步骤 | 操作 |
| :--- | :--- |
| **01 · 下载** | 在 [Releases](https://github.com/Oppie2333/neu-court-booking/releases/tag/v1.0.0) 下载 `NEUCourtBooking-1.0.0-windows-x64.zip`，完整解压后双击 `NEUCourtBooking.exe`。无需 Python，需已安装 **Google Chrome**。 |
| **02 · 设置** | 填写自己的预约日期、时段和同行人，点击“确认设置并开始”；在新打开的 Chrome 中自行登录学校。 |
| **03 · 等待** | 确认程序已就绪，保持电脑开机联网、不休眠。若生成付费订单，核对金额后由本人付款。 |

首次启动浏览器会联网获取匹配驱动，请提前准备。**GitHub 的 Code → Download ZIP 是源码**，普通用户请选择 Release 附件中的 Windows 运行包；保留解压目录的全部文件。

📖 完整操作、停止方法及故障处理见 [使用说明](docs/使用说明.md)。

## 🏸 可以帮你做什么

| 能力 | 具体行为 |
| :--- | :--- |
| **按偏好选择** | 免费首选默认 `15:30–17:00`；可开启 `14:00–21:00` 内 1 或 2 小时的付费备选，每场最多 30 元。日期、时段和预算均由使用者核对。 |
| **处理拥堵提示** | 反复确认已识别的网络异常弹窗；学校明确返回失败后，重试同一场次的“去支付”。请求串行执行，繁忙时退避。 |
| **核对预约结果** | 免费场核验订单日期、场地、时段及有效状态；付费场尝试打开付款窗口，再交给本人操作。结果未知时先查已有订单。 |
| **保留操作控制** | 默认不自动开始、不启用付费备选、不自动勾选条款。条款需每次独立授权，支持停止任务与运行截止时间。 |

默认放号时间为北京时间 **07:00**，运行 **15 分钟**；启用付费备选后，默认在放号 **15 秒**后允许选择付费场。收费按学生价 **15 元/小时**估算，支付页还会核对实际金额。请以学校当前规则为准。

登录、验证码和最终付款由本人完成。**待支付不代表预约成功**，最终以学校有效订单为准。运行期间不要同时手工提交同一任务。

## 📰 NEWS

**2026-10-19 · 项目介绍更新** — 新增像素 Q 版南湖校门与羽毛球运动页首插画，精简页面并补充场馆适用范围。

**2026-10-19 · v1.0.0** — 首个可分发测试版：中文设置界面、异常提示重试、订单核验、Windows 运行包与开源构建流程。

查看 [最新动态](NEWS.md) · [完整更新记录](CHANGELOG.md) · [发布说明模板](.github/RELEASE_TEMPLATE.md)

## 🛠️ 从源码运行

需要 Windows、Chrome 和 **Python 3.11+**（建议 3.12 x64）。双击 `启动源码版.cmd`，或在项目目录执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

<details>
<summary>开发检查与 Windows 构建</summary>

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

构建输出为 `dist/NEUCourtBooking`，分发时压缩整个目录。本机 46 项测试通过，打包程序的界面、Selenium Manager 和 Chrome 空白页启动检查通过；尚未在另一台干净电脑验证。

版本使用 `主版本.次版本.修订号`；程序版本见 `app_settings.py`，发布版本同步记录在 [VERSION](VERSION) 与 [CHANGELOG](CHANGELOG.md)。提交先记录在 `Unreleased`，发布时建立 `v版本号` 标签。详见 [发布到 GitHub](docs/发布到GitHub.md)。

</details>

## 🔒 数据与反馈

本机数据位于 `%LOCALAPPDATA%\NEUCourtBooking`，包括同行人设置、任务状态及诊断资料。分享软件时转发原始运行包即可；不要公开个人数据目录、密码、cookie、订单信息或支付码。反馈请使用 [Issue 模板](https://github.com/Oppie2333/neu-court-booking/issues/new?template=bug_report.md)，并先遮盖私人内容。

欢迎提交可复现的问题和改进。适配新场馆或修改学校流程时，请说明依据，并补充相应验证。

## 🤝 许可与致谢

程序与项目美术采用 [MIT License](LICENSE)，像素 Q 版页首插画由 AI 辅助生成，来源与设计说明见 [素材说明](docs/assets/ATTRIBUTION.md)。第三方运行组件保留各自许可，见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。

开发时审阅过 [Jian2032/neu_badminton_tool](https://github.com/Jian2032/neu_badminton_tool) 与 [MeowgicalAI/Auto-Reservation](https://github.com/MeowgicalAI/Auto-Reservation)，并根据学校实际页面独立实现本项目。参考仓库的完整授权未核实，本项目不分发其代码、驱动或可执行程序。
