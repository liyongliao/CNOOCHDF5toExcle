import subprocess
import sys
import os
import shutil

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
        "pyinstaller",
        "--onefile",
        f"--add-data=static{separator}static",
        "--name=H5ToExcelConverter",
        "run.py"
    ]
    
    print(f"执行打包命令: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n==================================================")
        print(" 🎉 打包成功完成！")
        if sys.platform == "win32":
            print(" Windows 可执行程序位于: dist\\H5ToExcelConverter.exe")
        else:
            print(f" 您的可执行程序位于: dist/H5ToExcelConverter")
        print("==================================================")
    except Exception as e:
        print(f"\n[错误] 打包失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build_executable()
