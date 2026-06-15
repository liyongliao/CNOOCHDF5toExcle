# 东方1-1气田井下压力转换工具 (DF11 H5 to Excel Converter)

🌊 **东方1-1气田井下压力转换工具** 是一款专为石油天然气行业中 HDF5 格式井下监测数据而设计的可视化转换、重采样与对齐工具。基于中国海油（CNOOC）主题视觉风格打造，具有极简的一键式默认参数导出体验，并支持高精度字段模糊搜索与批量多线程导出。

---

## ✨ 核心特性

- ⚓ **海油风格 UI**：采用专业的海油深海蓝（CNOOC Blue）与能源橙（CNOOC Orange）渐变配色，界面精美。
- 📊 **等间隔重采样对齐**：支持自动时间对齐与高精度插值算法，将异步采样的多字段数据完美对齐到统一时间轴（如 10S 间隔，可自定义）。
- 🌡️ **物理量单位转换**：
  - **温度单位**：支持 `degC` (摄氏度)、`degF` (华氏度)、`K` (开尔文) 之间的自动互转。
  - **压力单位**：支持 `PSI`、`Pa`、`kPa`、`bar`、`MPa` 之间的自动互转。
- 🔍 **千级字段极速检索**：新增高性能字段检索栏，支持对包含几百个数据通道的文件进行毫秒级实时搜索过滤。
- 🤖 **智能默认勾选**：打开文件时自动优先识别并勾选核心物理量（如 `EQRTZ S1 PRES PSI A` 与 `EQRTZ S1 TEMP CELSIUS A`），并智能匹配 fallback 压力与温度数据列。
- 🚀 **多线程批处理**：支持将多个 H5 文件加入任务队列，开启多线程并行处理，提供实时进度条监控。
- 📝 **元数据写头**：导出的 Excel/CSV 表格首行自动写出文件名解析得到的 Well name、Sn、Version 等元数据（兼容原有格式）。

---

## 🛠️ 环境准备与本地运行

如果您有 Python 环境，可以直接运行本地轻量级服务：

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 运行本地服务
```bash
python run.py
```
运行后在浏览器中打开：[http://127.0.0.1:8000](http://127.0.0.1:8000) 即可开始使用。

---

## 📦 如何在 Windows / macOS 下编译为单个可执行程序 (.exe)

本工具已内置 PyInstaller 编译脚本 [build.py](build.py)。

### 本地编译命令
```bash
# 安装打包工具
pip install pyinstaller

# 执行打包
python build.py
```
打包成功后，编译产物将生成在 `dist/` 文件夹下（Windows 下为 `H5ToExcelConverter.exe`）。

---

## ☁️ 使用 GitHub Actions 自动构建 Windows 可执行文件 (免安装打包环境)

本仓库集成了 GitHub Actions 自动化构建。您在 Mac 下提交代码至 GitHub 后，系统会在云端 Windows 服务器上完成打包：

1. 代码推送至本仓库的 `main` 分支。
2. 访问 GitHub 仓库顶部的 **`Actions`** 页面。
3. 点击 **`Build Windows Executable`** 构建流。
4. 在最新一次运行通过后，下拉至页面下方的 **`Artifacts`**，直接下载 **`H5ToExcelConverter-Windows`** 压缩包，解压后双击 `.exe` 即可在没有 Python 环境的 Windows 电脑上独立运行！

---

## 📄 许可证

本项目遵照 MIT 协议开源。
