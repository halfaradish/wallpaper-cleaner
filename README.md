# wallpaper-cleaner

自动清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件，释放存储空间；也可以在面板里批量取消订阅、把残留的壁纸重新订阅回来。

三种用法：**下载 exe 双击即用**（不需要装 Python）、**浏览器管理面板**、**命令行一键清理**。

## 下载 zip 解压即用（推荐）

到 [Releases](../../releases) 下载 `wallpaper-cleaner-<版本>.zip`，解压后会得到一个 `wallpaper-cleaner` 文件夹，双击里面的 `wallpaper-cleaner.exe` 即可。不需要安装 Python，也不需要命令行。

> 请先解压再运行，不要直接在压缩包里双击。程序会把配置与日志写在 exe 旁边（见[配置与日志](docs/配置与日志.md)），
> 直接双击压缩包里的 exe 时，Windows 会先把它解到临时目录再运行，配置就会跟着落在那个随时会被清掉的地方。

解压后那个文件夹就是它的全部家当：`config.yml`、`prefs.json`、`logs/` 都在里面。想换台机器用、或者想彻底删掉，挪走或删除这一个文件夹就够了。

启动后会弹出一个窗口：

1. 自动找到 Wallpaper Engine 的位置并扫描
2. 列出所有已取消订阅但仍占着磁盘的壁纸残留，以及能释放多少空间
3. 勾选要清理的，点清理按钮，确认后删到回收站

### 首次运行的两个提示

**启动要等几秒。** exe 是单文件打包的，每次启动需要先解压，视机器性能约 3-8 秒。期间没有反应是正常的，别重复双击（重复双击不会开出第二个窗口）。

**Windows 可能弹「已保护你的电脑」。** 这是因为没有买代码签名证书，属于正常现象。点「更多信息」→「仍要运行」即可。

### 需要 WebView2 运行时

窗口用的是 Windows 自带的 WebView2。Windows 11 和大部分 Windows 10 已经内置；如果提示缺少，从[微软官网](https://developer.microsoft.com/microsoft-edge/webview2/)免费装一次即可。

装不了也没关系，可以改用浏览器模式：在终端里执行 `wallpaper-cleaner.exe --web`。

## 快速开始

三种用法，按需要挑一个：

| 用法 | 最短上手方式 |
|------|--------------|
| 桌面窗口（推荐） | 下载 zip 解压后双击 `wallpaper-cleaner.exe`，见上一节 |
| 浏览器管理面板 | `wallpaper-cleaner.exe --web`，会自动打开 `http://127.0.0.1:8787/`，只监听本机；面板能做什么见[管理面板](docs/管理面板.md) |
| 命令行清理 | 先 `--dry-run` 预览将要删除的内容，确认无误后去掉 `--dry-run` 再跑一次；全部参数见[命令行](docs/命令行.md) |

从源码运行的话把上面命令里的 `wallpaper-cleaner.exe` 换成 `python wallpaper-cleaner.py`，环境要求是 Windows + Python 3.7 及以上；桌面窗口模式额外需要 `pywebview`（Python 3.8+），命令行与浏览器面板不需要任何第三方依赖。配置与日志放在哪见[配置与日志](docs/配置与日志.md)。

清理默认是删到回收站，误删可以从回收站恢复；删除前的各项守卫见[安全提示](docs/安全提示.md)。

### 延伸阅读

- [工作原理](docs/工作原理.md)：怎么判断哪些壁纸该清理
- [命令行](docs/命令行.md)：运行环境、首次运行、全部参数
- [管理面板](docs/管理面板.md)：面板功能、两个大小口径、取消订阅与重新订阅
- [配置与日志](docs/配置与日志.md)：`config.yml` 字段与文件位置
- [安全提示](docs/安全提示.md)：程序只写什么、不碰什么
- [已核实事实](docs/已核实事实.md)：对 Wallpaper Engine / Steam 行为的实测结论
- [开发](docs/开发.md)：目录结构、运行测试、打包 exe、发版流程

## 开发

```bash
python -m unittest discover -s tests -v   # 运行测试
python packaging/build.py                 # 打包：建 .venv-build、装依赖、跑测试、出 exe 与 zip、冒烟测试
python packaging/build.py --skip-deps     # 依赖已就绪时只重新打包
```

测试全部在临时目录沙箱内构造伪造目录树，不会触碰真实配置与真实 Steam 目录。

换程序图标、发版流程、代码签名与目录结构见[开发文档](docs/开发.md)。

## 许可协议

采用 [MIT License](LICENSE)，版权归 halfaradish 所有。

桌面窗口模式依赖 pywebview（BSD-3-Clause）、pythonnet 与 clr_loader（MIT），命令行与浏览器面板模式只用 Python 标准库。这些依赖的许可均为宽松许可，与本项目协议兼容。

「借本机已安装游戏的 `steam_api64.dll` 连接 Steam 客户端」这一思路参考了 [xiaoyuyu6420/wallpaper-engine-cleaner](https://github.com/xiaoyuyu6420/wallpaper-engine-cleaner)（MIT）；本项目的 `steamapi.py` 是独立实现（改用 Wallpaper Engine 自带的 dll、加锁串行化调用、以轮询订阅列表确认结果），未复制其代码。
