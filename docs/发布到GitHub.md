# 发布到 GitHub

本文件以 **1.1.0**（**2026-10-23**）的 **预发布测试版（pre-release）** 为例，说明本地 Git、源码上传与运行包发布流程。目标仓库为 [Oppie2333/neu-court-booking](https://github.com/Oppie2333/neu-court-booking)。已有仓库请从对应步骤继续，勿重复初始化。

## 发布源码

1. 使用这个整理后的 `neu-court-booking` 源码目录创建 Git 仓库。不要用个人运行目录或旧的工作区替代。
2. 检查待提交文件。应包含程序源码、测试、构建脚本、README、NEWS、CHANGELOG、VERSION、使用说明、公开设计素材及许可文件；不应包含个人配置、私人截图、日志、网页缓存、cookie、浏览器资料、虚拟环境或旧任务文件。
3. 将仓库上传到自己新建的 GitHub 仓库。首次检查 GitHub 文件列表，确认只包含准备公开的文件。
4. 在仓库简介中说明适配南湖羽毛球馆、Windows 和人工登录付款，并保留当前验证限制。

登录自己的 GitHub 账号，创建名为 `neu-court-booking` 的公开空仓库，不额外初始化 README、许可证或 `.gitignore`。若名称已存在，先确认是否为目标仓库；改名时同步修改远程地址和文档链接。

安装 Git，在整理后的项目目录打开终端，按顺序执行。暂存后先检查内容，确认没有私人资料：

```powershell
git init -b main
git add .
git status --short
git diff --cached --stat
git diff --cached
git commit -m "Release v1.1.0"
git remote add origin https://github.com/Oppie2333/neu-court-booking.git
git push -u origin main
```

GitHub 认证由本人完成。如果 Git 提示未设置提交者姓名和邮箱，请用自己的账号信息配置。目录已是 Git 仓库或已配置 `origin` 时，先核对现有状态并跳过对应初始化步骤，不覆盖不明远程地址。上传后检查 GitHub 文件列表、README 图片和文档链接；`.gitignore` 不能替代隐私检查。

项目原创代码以 MIT 发布。`LICENSE` 不替代依赖许可，也不为参考仓库提供授权。如果以后引入别人的代码，需要先核对许可证，再保留相应声明。

## 构建与检查

在 Windows 64 位系统上安装 Python 3.12 和 Chrome。需要试用源码时，可双击项目目录的 `启动源码版.cmd`，它会创建 `.venv` 并安装运行依赖，也可以在已经安装依赖的环境运行 `python main.py`。

构建前打开项目目录运行：

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

`build.ps1` 默认使用名为 `python` 的解释器，会安装构建依赖、运行测试并生成 `dist/NEUCourtBooking` 文件夹。也可用 `-Python` 指定独立环境，例如：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Python .venv/Scripts/python.exe
```

将完整目录压缩成当前版本运行包，并生成配套校验值：

```powershell
Compress-Archive -LiteralPath dist/NEUCourtBooking -DestinationPath NEUCourtBooking-1.1.0-windows-x64.zip
$packageHash = (Get-FileHash -Algorithm SHA256 NEUCourtBooking-1.1.0-windows-x64.zip).Hash.ToLowerInvariant()
"$packageHash  NEUCourtBooking-1.1.0-windows-x64.zip" | Set-Content -Encoding ascii SHA256SUMS.txt
```

保留程序目录中的 `_internal`、使用说明和 `third_party_licenses` 等文件，不要只上传 exe。源码可用 GitHub 自动提供的源码压缩包，Windows zip 作为独立附件上传。代码提交时不提交 `dist`、构建缓存和虚拟环境。

如果已有最终 zip 和 `SHA256SUMS.txt`，使用实际对应的这一组产物。重新压缩后必须重新计算校验值，不将旧校验值配给新文件。

发布前在干净的 Windows 用户环境或另一台电脑上打开解压后的 exe，至少确认：

- 未安装 Python 时也能打开中文界面，Chrome 能被启动。
- 首次配置不含任何人的姓名、学号、条款授权和已启用任务。
- 默认不启动，必须由使用者核对日期并开始；已开始的任务不会自动切换到次日。日期框在 07:00 后会预填次日，仅作为建议。
- 数据写入当前用户的 `%LOCALAPPDATA%\NEUCourtBooking`。
- 停止按钮有效，关闭后不会留下继续下单的后台程序。
- 使用说明、许可证、第三方许可资料与程序一并分发。

不必为检查而创建真实预约。真实学校第二层支付页尚未完整验证，不应将本地模拟测试写成真实高峰成功率证明。

## 创建 Release

确认源码与最终产物一致后，创建并推送当前版本标签：

```powershell
git tag -a v1.1.0 -m "v1.1.0 prerelease"
git push origin v1.1.0
```

在目标仓库打开 **Releases → Draft a new release**：

1. 选择标签 `v1.1.0`，标题填写 `v1.1.0 · 三种预约模式与查询间隔设置`。
2. 复制 [.github/RELEASE_TEMPLATE.md](../.github/RELEASE_TEMPLATE.md) 正文，核对日期、验证结果及附件。
3. 上传完整 `NEUCourtBooking-1.1.0-windows-x64.zip` 和对应的 `SHA256SUMS.txt`。整理后的源码 zip 可作为额外附件；GitHub 也会提供标签对应的源码下载。
4. 勾选 **Set as a pre-release**，核对后再发布。

不要把个人数据、日志或内存转储当作 Release 附件。

可在发布说明中使用以下要点，并按实际验证结果补充：

- 适用 Windows 10/11 x64，已安装 Chrome，无需 Python。
- 首次浏览器启动需联网获取驱动；登录、验证码和最终付款由用户完成。
- 单日期、最多一份订单；明确失败后重试，结果未知时先查单。
- 免费订单会核验；付费第二层网页尚未完整现场验证。
- 本地自动测试和模拟网页测试的实际结果；不承诺预约成功率。

发布成功后核对 NEWS、README 的版本与下载地址。当前尚未完成另一台电脑的兼容性验证，因此保持预发布标记；不要将本机检查写成真实高峰预约成功。

## 维护

学校页面、收费标准或规则变化时，优先停用受影响流程并更新校验。问题反馈请使用仓库模板，提醒提交者遮盖个人信息。不要在 issue 中要求密码、cookie 或完整登录后的网页源码。

新版本先在 CHANGELOG 的 `Unreleased` 中记录用户可见变化，准备发布时移入带版本号与日期的条目。同步软件版本、NEWS、附件名称和发布说明。
