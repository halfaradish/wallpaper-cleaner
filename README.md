# wallpaper-cleaner

自动清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件，释放存储空间。

三种用法：**下载 exe 双击即用**（不需要装 Python）、**浏览器管理面板**、**命令行一键清理**。

## 直接下载使用（推荐）

到 [Releases](../../releases) 下载 `wallpaper-cleaner.exe`，双击运行即可。不需要安装 Python，也不需要命令行。

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

## 环境要求

源码运行：

- Windows 系统（Steam 路径自动检测依赖 Windows 注册表）
- Python 3.6 及以上版本
- 命令行与浏览器面板模式**不需要任何第三方依赖**；桌面窗口模式需要 `pywebview`

## 工作原理

1. 读取 Wallpaper Engine 的 `workshopcache.json`，获取当前所有已订阅壁纸的 workshop ID
2. 遍历 workshop 内容目录，对比本地文件夹与订阅列表
3. 删除不在订阅列表中的文件夹，保留已订阅的壁纸

## 管理面板

```bash
python wallpaper-cleaner.py --web        # 浏览器模式
python wallpaper-cleaner.py --desktop    # 桌面窗口模式（需 pywebview）
```

浏览器模式下会自动打开 `http://127.0.0.1:8787/`，只监听本机回环地址，其他设备无法访问。

### 面板能做什么

- **概览**：已订阅数量、磁盘文件夹数量、待清理数量、可释放空间
- **待清理列表**：列出所有不在订阅列表中的目录，可勾选后删除。删除前会弹出确认框，逐条列出将要删除的内容与总大小
- **删除到回收站**：默认把目录移入回收站而不是永久删除，误操作可以从回收站恢复
- **已订阅列表**：查看订阅中的壁纸标题、标注大小与实际占用，支持按 ID / 标题筛选
- **设置**：编辑两个路径，支持一键自动检测，保存时会保留配置文件里的注释
- **日志**：直接查看最新日志文件

### 目录分类

面板会把不在订阅列表中的目录分成两类，避免误删：

| 类型 | 判定 | 默认 |
|------|------|------|
| **孤儿目录** | 目录名是纯数字（符合 workshop ID 格式） | 勾选「全选孤儿目录」时会被选中 |
| **未知目录** | 目录名不是纯数字，来源不明（如手动备份） | 永远不会被全选选中，必须逐个手动勾选 |

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--desktop` | 启动桌面窗口（需要 pywebview） |
| `--web` | 启动浏览器管理面板 |
| `--port` | 监听端口。桌面窗口默认由系统分配空闲端口，浏览器模式默认 `8787` |
| `--host` | 监听地址，默认 `127.0.0.1`（仅本机可访问） |
| `--no-browser` | 浏览器模式下不自动打开浏览器 |
| `--dry-run` | 只预览将要删除的内容，不实际删除 |
| `--version` | 输出版本号 |

```bash
python wallpaper-cleaner.py --web --port 9000 --no-browser
python wallpaper-cleaner.py --dry-run
python wallpaper-cleaner.py --help
```

## 命令行使用

### 首次运行

直接运行脚本，程序会自动检测 Wallpaper Engine 路径：

```bash
python wallpaper-cleaner.py
```

- **自动检测成功**：展示检测到的路径，确认后直接执行清理
- **自动检测失败**：自动生成 `config.yml`，按提示编辑配置文件后重新运行

不带参数时命令行会**立即删除**所有未订阅的目录（包括未知目录），不提供预览。想先看清楚将删除什么，请用 `--dry-run` 或管理面板。

### 配置文件 (`config.yml`)

配置采用 YAML 格式，支持用 `#` 编写注释：

```yaml
# Wallpaper Engine 的 workshop 订阅缓存文件路径
json_path: D:\Steam\steamapps\common\wallpaper_engine\bin\workshopcache.json

# workshop 壁纸内容存放目录
workshop_dir: D:\Steam\steamapps\workshop\content\431960
```

| 字段 | 说明 |
|------|------|
| `json_path` | Wallpaper Engine 的 workshop 缓存文件路径 |
| `workshop_dir` | workshop 壁纸内容存放目录 |

路径支持绝对路径和相对路径（相对路径基于配置所在目录）。Windows 路径可不加引号直接书写（引号内的内容不会被转义处理）。

> 兼容说明：旧版 `config.json` 仍会被自动读取；重命名为 `config.yml` 后即可使用注释功能。

## 配置文件与日志放在哪

| 运行方式 | 位置 |
|---|---|
| 源码运行 | 项目根目录下的 `config.yml` 与 `logs/` |
| 运行 exe | `%APPDATA%\wallpaper-cleaner\`（即 `C:\Users\<你的用户名>\AppData\Roaming\wallpaper-cleaner`） |
| 设了环境变量 | `WALLPAPER_CLEANER_HOME` 指定的目录 |

exe 不能把配置写在自己旁边——它可能被放在 `Program Files` 这类没有写权限的位置，而单文件模式解压出的临时目录会随进程消失。想做成便携版（比如放 U 盘里）就设置 `WALLPAPER_CLEANER_HOME`。

## 目录结构

```
wallpaper-cleaner.py            # 入口：无参数走命令行，--desktop/--web 走图形界面
wallpaper_cleaner/
  core.py                       # 路径检测、配置读写、扫描、删除（纯逻辑，不打印不退出）
  cli.py                        # 命令行流程
  web.py                        # 面板服务端（标准库 http.server）
  desktop.py                    # 桌面窗口（WebView2）、单实例守卫、控制台接管
  static/                       # 面板前端（原生 HTML/CSS/JS，无构建步骤）
packaging/
  wallpaper-cleaner.spec        # PyInstaller 打包配置
  build.py                      # 一键构建 exe
  smoke_test.py                 # 打包产物冒烟测试
tests/test_core.py              # 单元测试
```

## 日志

每次运行会在 `logs/` 目录下生成日志文件，记录删除和保留的详细信息。日志按天分文件，单文件最大 5MB，保留 5 个备份。

## 安全提示

- 程序仅对比订阅列表与本地文件，不会删除已订阅的壁纸
- 删除前会重新读取一次订阅列表：若某个壁纸在扫描后又被重新订阅，会被自动跳过
- 管理面板的删除操作有三层校验：路径必须是 `workshop_dir` 的直接子目录、必须是最近一次扫描确认过的待清理项、必须携带本次启动的访问令牌
- 清理任务进行中不允许关闭桌面窗口，避免进程退出让删除停在半路
- 建议首次运行前备份重要壁纸文件

## 开发

### 运行测试

```bash
python -m unittest discover -s tests -v
```

测试全部在临时目录沙箱内构造伪造目录树，不会触碰真实配置与真实 Steam 目录。

### 打包 exe

需要 Python 3.8+：

```bash
python packaging/build.py
```

脚本会自动在项目下建 `.venv-build` 虚拟环境、装好 PyInstaller 与 pywebview（不污染全局 Python），跑完单测后打包，最后对产物做冒烟测试。产物在 `dist/wallpaper-cleaner.exe`。

只重新打包（依赖已就绪）：

```bash
python packaging/build.py --skip-deps
```

### 发版流程

1. 更新 `wallpaper_cleaner/__init__.py` 里的 `__version__`
2. 提交并打标签：`git tag v2.0.1 && git push origin v2.0.1`
3. GitHub Actions 会自动跑测试、校验标签与版本号一致、打包、冒烟测试，然后创建 Release 并把 exe 和 `SHA256SUMS.txt` 传上去

标签和代码里的版本号不一致时构建会直接失败，避免发出版本号对不上的包。工作流也支持手动触发，此时只构建并上传 artifact，不发布 Release。

### 代码签名

当前 exe 未签名，用户首次运行会遇到 SmartScreen 提示。要消除它需要购买代码签名证书，配置好后在 `.github/workflows/release.yml` 的发布步骤前插入签名步骤即可（文件里已留了位置）。
