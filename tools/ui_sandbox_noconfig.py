"""以「找不到 Wallpaper Engine」的状态启动面板，用来验证未配置时的界面

真实机器上装了 WE，面板会自动探测到它，因此走不到 source='none' 那条分支。
这里只把探测函数替换成"什么都没找到"，其余逻辑一律不动。

注意：core 在导入时就把配置路径算好了（script_dir = app_home_dir()），
所以 WALLPAPER_CLEANER_HOME 必须在 import 之前设好，否则沙箱不生效。

用法：python tools/ui_sandbox_noconfig.py <空目录> <端口>
"""
import os
import sys

home = os.path.abspath(sys.argv[1])
os.makedirs(home, exist_ok=True)
os.environ['WALLPAPER_CLEANER_HOME'] = home

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wallpaper_cleaner import core, web  # noqa: E402  必须在设置环境变量之后导入

core.auto_detect_paths = lambda: (None, None)

sys.exit(web.run(host='127.0.0.1', port=int(sys.argv[2]), open_browser=False))
