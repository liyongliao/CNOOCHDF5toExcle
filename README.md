# 井下压力 HDF5 转 Excel / CSV

免安装的 Windows 桌面工具。解压整个软件文件夹后，双击 `H5ToExcelConverter.exe` 即可使用，不需要安装 Python、浏览器插件或 WebView。

## 使用

1. 选择 H5 文件或文件夹，勾选要导出的文件。
2. 选择保存目录与 Excel / CSV 格式。
3. 点击开始导出。默认选取井下压力、温度字段，按 10 秒间隔对齐。

“高级设置”可调整时间间隔与单位；文件设置可选择字段、时间列、时间范围和输出文件名。支持字段预设、批量处理、实时进度与取消。Excel 数值保留两位小数，CSV 使用 UTF-8，首行保留井名、序列号和版本信息。时间按文件中的 UTC 时间轴解释，与原版本一致。

## Windows 下载与自动构建

[GitHub Actions 构建页面](https://github.com/liyongliao/CNOOCHDF5toExcle/actions/workflows/build.yml)

推送到 `main`、`master` 或 `codex/**` 分支，创建拉取请求，或在 Actions 点击 **Run workflow**，都会自动在 Windows 云端构建和验证两个版本。Mac 只需提交代码，不承担 Windows 编译。

| 下载包 | 适用电脑 |
| --- | --- |
| `H5ToExcelConverter-Windows-x64.zip` | 推荐：64 位 Windows 10 / 11 |
| `H5ToExcelConverter-Windows-x86.zip` | 需要 32 位程序的 Windows 电脑，也可在 64 位 Windows 上运行 |

在成功运行的 **Artifacts** 下载对应文件。解压下载包，再解压其中的软件 ZIP；保留 `H5ToExcelConverter` 文件夹内的 `_internal` 等所有内容，双击 `.exe`。`sha256` 文件用于校验下载包。

默认采用目录打包：运行时直接读取依赖，省去单文件模式每次启动的临时解压。界面只加载 Python 自带桌面组件，数据引擎在窗口显示后加载；不需要启动本地服务器。导出按块处理、流式写入 Excel / CSV，取消任务后清理临时文件，成功后才替换最终文件。

本机优化前后对比：同一 Mac、独立进程、合成 HDF5 的 150,000 行压力/温度导出为 Excel，旧版耗时 5.179 秒、峰值内存 370.9 MiB；新版耗时 5.021 秒、峰值内存 81.2 MiB，峰值内存降低约 78%。测试包含导出引擎的内存占用，不使用生产数据；Windows 的实际性能取决于电脑和数据。

32 位版使用 Python 3.8.10、h5py 2.10.0 与 NumPy 1.23.5 的兼容组合，因为后续 h5py 不再提供官方 Windows 32 位安装包。64 位版使用 Python 3.11 和较新的数据引擎。两者都在云端执行转换测试、窗口启动测试和打包后真实导出测试。CI 报告中的启动时间来自云端测试电脑，不代表所有电脑的速度。

## 设置保存

软件优先将设置保存到同目录的 `config.json`；目录不可写时保存到 `%LOCALAPPDATA%/H5ToExcelConverter/config.json`。发布包内的 `config.default.json` 只含通用预设，不包含开发电脑路径或生产数据。更新软件时可保留个人 `config.json`。

## 源码运行与本地打包

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
```

Python 需包含 Tk 桌面组件；Windows 官方 Python 自带该组件。Mac 上缺少 Tk 时可安装对应 Python 的 Tk 支持，或使用保留的浏览器界面：

```sh
python run.py --web
```

在 Windows 上本地打包（32 位构建需使用 32 位 Python 3.8.10 和对应依赖文件）：

```sh
python -m pip install -r requirements.txt -r requirements-build.txt
python build.py
```

输出为 `dist/H5ToExcelConverter/`。可选 `python build.py --onefile` 生成单文件版本，该模式每次启动需要解压，打开速度较慢。`--console` 可保留调试控制台。

## 验证

```sh
python -m unittest discover -s tests -v
python run.py --self-test --report test-reports/self-test.json
python run.py --smoke-test --report test-reports/smoke-test.json
```

自检使用临时生成的 HDF5 数据，验证扫描、复合字段、Excel / CSV 数值及数值格式，不读取生产文件。桌面启动测试需要桌面环境；GitHub 工作流还会对打包后的 Windows EXE 重新验证。
