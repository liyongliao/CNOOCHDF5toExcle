import sys
import os
import subprocess
import socket
import webbrowser
import time
import threading

# 获取当前脚本所在目录和 venv 的 python 路径
base_dir = os.path.dirname(os.path.abspath(__file__))
venv_dir = os.path.join(base_dir, "venv")

if sys.platform == "win32":
    venv_python = os.path.join(venv_dir, "Scripts", "python.exe")
else:
    venv_python = os.path.join(venv_dir, "bin", "python")

def check_and_setup_venv():
    # 如果是打包后的可执行文件 (PyInstaller)，则无需配置或进入虚拟环境
    if getattr(sys, 'frozen', False):
        return

    # 如果当前运行的不是 venv 中的 python，并且 venv 的 python 已经存在
    if sys.executable != venv_python and os.path.exists(venv_python):
        print("[提示] 正在切换至本地虚拟环境运行...")
        # 启动 venv 的 python 重新运行当前脚本并传递参数，然后退出
        sys.exit(subprocess.call([venv_python] + sys.argv))

    # 如果 venv 的 python 不存在，则创建并安装依赖
    if not os.path.exists(venv_python):
        print("==================================================")
        print("首次运行，正在自动为您构建虚拟隔离环境 (venv)...")
        print("==================================================")
        try:
            # 创建虚拟环境
            subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
            print("\n虚拟环境创建成功，正在安装依赖项，请稍候...")
            
            # 使用虚拟环境的 pip 升级并安装依赖
            req_file = os.path.join(base_dir, "requirements.txt")
            subprocess.check_call([venv_python, "-m", "pip", "install", "--upgrade", "pip"])
            subprocess.check_call([venv_python, "-m", "pip", "install", "-r", req_file])
            print("==================================================")
            print(" 依赖项安装成功！")
            print("==================================================")
        except Exception as e:
            print(f"\n[错误] 初始化虚拟环境或安装依赖失败: {e}")
            print("您可以尝试在终端手动配置:")
            print("  python3 -m venv venv")
            print("  source venv/bin/activate")
            print("  pip install -r requirements.txt")
            sys.exit(1)
            
        # 安装成功后，使用虚拟环境中的 python 重新执行当前脚本并退出
        sys.exit(subprocess.call([venv_python] + sys.argv))

# 自动处理虚拟环境引导
check_and_setup_venv()

# ==================================================
# 以下为虚拟环境中执行的核心业务逻辑
# ==================================================

def find_free_port(start_port=8000):
    port = start_port
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except socket.error:
                port += 1
    raise RuntimeError("未找到可用的网络端口")

def main():
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    
    print("==================================================")
    print(f"  井下压力 HDF5 转换 Excel 工具")
    print(f"  本地服务地址: {url}")
    print("==================================================")
    
    def open_browser():
        time.sleep(1.5)
        print(f"\n[提示] 正在启动您的浏览器以打开可视化界面...")
        webbrowser.open(url)
        
    # 在后台线程中开启浏览器，防止阻塞服务器启动
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    
    # 启动 FastAPI / Uvicorn 服务器
    try:
        from app import app
        import uvicorn
        # 传入 app 对象而不是 "app:app" 字符串，方便 PyInstaller 追踪依赖并动态打包
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info", reload=False)
    except KeyboardInterrupt:
        print("\n已安全关闭本地服务。感谢使用！")
    except Exception as e:
        print(f"\n[错误] 启动本地服务失败: {e}")

if __name__ == "__main__":
    main()
