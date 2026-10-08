# 井下压力 HDF5 转 Excel / CSV

Windows 独立桌面软件，支持 HDF5 压力、温度数据批量导出。2.1.0 使用 CustomTkinter 绘制现代界面：浅色卡片、圆角按钮、清晰的主操作和实时进度。安装包包含运行所需依赖，使用电脑无需安装 Python、浏览器插件或 WebView。

## 下载与使用

[GitHub Actions 构建页面](https://github.com/liyongliao/CNOOCHDF5toExcle/actions/workflows/build.yml)

| 文件 | 用途 |
| --- | --- |
| `H5ToExcelConverter-Setup-x64.exe` | 推荐：64 位 Windows 10 / 11，安装后从桌面或开始菜单打开 |
| `H5ToExcelConverter-Setup-x86.exe` | 需要 32 位程序的 Windows 电脑，也可在 64 位 Windows 上运行 |
| `H5ToExcelConverter-Windows-x64.zip` | 64 位免安装版，完整解压后运行 |
| `H5ToExcelConverter-Windows-x86.zip` | 32 位免安装版，完整解压后运行 |

安装版由 Inno Setup 生成。双击对应架构的安装程序，按提示安装到当前用户的应用目录，无需管理员权限。安装程序创建桌面与开始菜单快捷方式；x64 / x86 使用独立安装目录，可以并存。安装时一次性解压运行文件，之后启动直接读取已安装的依赖，保留目录打包的打开速度。

免安装版将下载的 ZIP 完整解压到一个独立文件夹，打开该文件夹，双击 `H5ToExcelConverter.exe`。ZIP 根目录直接包含 EXE、`_internal` 和说明文件，解压一次即可使用。保留同目录的 `_internal` 文件夹和里面的文件。软件文件的作用见随包提供的 `软件文件说明.txt`。

在成功构建记录的 **Artifacts** 下载对应版本。安装版下载内容是 GitHub 生成的 ZIP，解压后得到一个 Setup EXE 和旁边的 `.sha256` 校验文件，运行该 EXE 即可安装。免安装版下载内容是 GitHub 直接压缩完整软件目录生成的 ZIP，完整解压一次即可运行。安装完成或免安装版解压完成后，下载用的 ZIP、安装程序和校验文件可移走。

1. 选择 H5 文件或文件夹，勾选要导出的文件。
2. 选择保存目录与 Excel / CSV 格式。
3. 点击“开始导出”。默认选取井下压力、温度字段，按 10 秒间隔对齐。

“采样与单位”可调整时间间隔、温度及压力单位；“字段 / 时间设置”可选择字段、时间列、时间范围和输出文件名。支持字段预设、批量处理、实时进度与取消。Excel 数值保留两位小数，CSV 使用 UTF-8，首行保留井名、序列号和版本信息。时间按文件中的 UTC 时间轴解释。

## Mac 与 Windows 的编译、测试范围

Mac 负责编辑代码、运行跨平台 Python 源码测试以及提交到 GitHub。Mac 上的源码测试能检查 HDF5 读取、时间对齐、单位转换和 Excel / CSV 导出；运行桌面源码需要该 Mac 的 Python 带有 Tk 支持。Windows EXE 的构建、启动和打包后导出测试由 GitHub 的 Windows 云端环境执行。

无需在这台 Mac 上安装 Windows 或运行本机虚拟机。GitHub 提供的 Windows 执行环境本身是云端托管虚拟机，由 GitHub 管理，详见 [GitHub 官方说明](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners)。本项目使用 `windows-2022`，分别安装对应架构的 Python，生成 x64 与 x86 程序，并直接运行生成的 Windows EXE 验证。

推送到 `main`、`master` 或 `codex/**` 分支，创建面向 `main` / `master` 的拉取请求，推送 `v*` 标签，或在 Actions 点击 **Run workflow**，都会触发云端构建。工作流运行转换测试、源码导出自检、打包后的窗口启动及 HDF5 → Excel / CSV 自检，并保存验证报告与窗口截图。通过构建后的安装程序也由工作流验证。

报告中的 Windows 启动时间来自云端测试电脑，不能等同于本机 Mac 测量，也不能代表所有 Windows 电脑。实际包大小和启动耗时以对应构建记录的摘要、下载文件和测试报告为准。

此前的导出引擎优化对比在同一台 Mac、独立 Python 进程中进行，使用临时合成 HDF5 数据，将 150,000 行压力 / 温度导出为 Excel：旧版耗时 5.179 秒、峰值内存 370.9 MiB；优化版耗时 5.021 秒、峰值内存 81.2 MiB，峰值内存降低约 78%。这个结果反映源码导出引擎的内存占用；数据来源、磁盘与硬件会影响 Windows 实际表现。

## 性能与软件文件

窗口先显示，HDF5 / NumPy 数据引擎随后在后台加载。导出按块处理，流式写入 Excel / CSV，取消任务后清理临时文件，成功后才替换最终文件。

桌面版本直接使用 `converter.py` 转换核心。浏览器界面由可选的 `app.py` 适配器提供，FastAPI、Pydantic、Uvicorn 等 Web 依赖单独放在 `requirements-web.txt`，Windows 桌面发行包只包含桌面、HDF5 与导出所需组件。

`_internal` 保存已经随软件提供的 Python 运行环境、HDF5 / NumPy 二进制库、桌面组件和主题。`base_library.zip` 是 Python 基础库，程序直接从中加载模块，保持压缩形式可以减少文件数量。两者都属于软件的运行部分，应完整保留。

“独立软件”表示依赖已经随包提供，使用电脑无需另外安装它们。安装版把依赖放在应用安装目录；免安装版把依赖放在解压后的软件目录。PyInstaller 单文件模式仍会在每次启动时解压依赖到临时目录，通常更慢，详见 [PyInstaller 官方说明](https://pyinstaller.org/en/stable/operating-mode.html)。本项目主要提供安装版与免安装目录版。

32 位版使用 Python 3.8.10、h5py 2.10.0 与 NumPy 1.23.5 的兼容组合；后续 h5py 不再提供官方 Windows 32 位安装包。64 位版使用 Python 3.11 和较新的数据引擎。两种架构分别在云端验证。

## 设置保存

软件优先将设置保存到程序同目录的 `config.json`；目录不可写时保存到 `%LOCALAPPDATA%\H5ToExcelConverter\config.json`。这个 AppData 回退目录可被两种架构读取，优先使用程序同目录已有设置。发布包内的 `config.default.json` 只含通用预设，不包含开发电脑路径或生产数据。更新或迁移免安装版时可保留个人 `config.json`。

## 源码运行与本地打包

64 位 Windows 或 Mac 的源码运行：

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Python 需包含 Tk 桌面组件；Windows 官方 Python 自带该组件。Mac 缺少 Tk 时需安装与该 Python 对应的 Tk 支持。可选浏览器界面需要单独安装 Web 依赖（开发环境使用 Python 3.11）：

```sh
python -m pip install -r requirements-web.txt
python run.py --web
```

在 Windows 上本地打包应用目录：

```sh
python -m pip install -r requirements.txt -r requirements-build.txt
python build.py
```

32 位构建需使用 32 位 Python 3.8.10，并将上述 `requirements.txt` 改为 `requirements-win-x86.txt`。应用目录输出为 `dist/H5ToExcelConverter/`；GitHub 工作流继续生成安装程序与免安装 ZIP。`--console` 可保留调试控制台。Windows 发行版需要在 Windows 上构建；在 Mac 执行本地打包只会生成 Mac 平台程序。

## 验证

```sh
python -m unittest discover -s tests -v
python run.py --self-test --report test-reports/self-test.json
python run.py --smoke-test --report test-reports/smoke-test.json
```

自检使用临时生成的 HDF5 数据，验证扫描、复合字段、Excel / CSV 数值及数值格式，不读取生产文件。桌面启动测试需要可用的图形桌面与 Tk。未安装可选 Web 依赖时，Web 适配器测试会跳过；需要验证浏览器接口时先安装 `requirements-web.txt`。GitHub 工作流在 Windows 重新运行打包后的 EXE 自检，安装版的验证范围见对应构建记录。
