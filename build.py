import subprocess
import sys
import os
import shutil

# 强制标准输出与标准错误使用 UTF-8 编码，防止 Windows 虚拟机控制台 cp1252 编码报错
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

def build_executable():
    print("==================================================")
    print(" 正在开始打包 HDF5 to Excel 可视化可执行程序")
    print("==================================================")
    
    # 确保我们在项目根目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)
    
    # 检查并安装 PyInstaller
    try:
        import PyInstaller
        print("检测到 PyInstaller 已经安装。")
    except ImportError:
        print("未检测到 PyInstaller，正在通过 pip 安装...")
        try:
            # 优先使用当前 python 环境的 pip 安装
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
            print("PyInstaller 安装成功。")
        except Exception as e:
            print(f"安装 PyInstaller 失败: {e}。请手动运行 'pip install pyinstaller' 后再次执行当前脚本。")
            sys.exit(1)
            
    # 确定平台专用的数据分割符
    # Windows 使用 ; 分割，macOS/Linux 使用 : 分割
    separator = ";" if sys.platform == "win32" else ":"
    
    # 构建打包命令
    # --onefile: 打包为单一可执行文件
    # --add-data: 包含前端静态文件文件夹 (源路径:目标路径)
    # --name: 可执行文件名称
    # --windowed / --noconsole: 对于 FastAPI 服务类桌面应用，通常需要保留命令行以显示日志，因此不加 --noconsole
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        f"--add-data=static{separator}static",
        f"--add-data=config.json{separator}.",
        "--name=H5ToExcelConverter",
        "run.py"
    ]
    
    print(f"执行打包命令: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        
        # 自动将 config.json 复制到 dist 目录，确保打包产物同级目录下有配置文件
        dist_dir = os.path.join(base_dir, "dist")
        src_config = os.path.join(base_dir, "config.json")
        dist_config = os.path.join(dist_dir, "config.json")
        if os.path.exists(src_config):
            os.makedirs(dist_dir, exist_ok=True)
            shutil.copyfile(src_config, dist_config)
            print(f"已自动复制 config.json 至 dist 目录: {dist_config}")
            
        print("\n==================================================")
        print(" 🎉 打包成功完成！")
        if sys.platform == "win32":
            print(" Windows 可执行程序位于: dist\\H5ToExcelConverter.exe")
            print(" 配置文件位于: dist\\config.json")
        else:
            print(f" 您的可执行程序位于: dist/H5ToExcelConverter")
            print(f" 配置文件位于: dist/config.json")
        print("==================================================")
    except Exception as e:
        print(f"\n[错误] 打包失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build_executable()
