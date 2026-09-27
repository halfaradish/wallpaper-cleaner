# wallpaper-cleaner

自动清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件，释放存储空间；也可以在面板里批量取消订阅、把残留的壁纸重新订阅回来。

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
- Python 3.7 及以上版本（控制台编码处理用到 `TextIOWrapper.reconfigure`）
- 命令行与浏览器面板模式**不需要任何第三方依赖**；桌面窗口模式需要 `pywebview`（要求 Python 3.8+）

## 工作原理

1. 读取 Wallpaper Engine 的 `workshopcache.json`，获取当前所有已订阅壁纸的 workshop ID（标题与标注大小也来自这里）
2. 遍历 workshop 内容目录，对比本地文件夹与订阅列表
3. 删除不在订阅列表中的文件夹，保留已订阅的壁纸

### 订阅状态是怎么判断的

「已订阅」由三路信息交叉判断：

| 来源 | 作用 |
|------|------|
| `workshopcache.json` | 订阅列表的主来源，标题与标注大小取自这里 |
| Steam 的 `appworkshop_431960.acf` | 交叉校验。其中 `WorkshopItemDetails` 里带 `subscribedby` 的条目（部分 Steam 版本还有 `WorkshopItemsSubscribed` 小节）是**订阅记录**；`WorkshopItemsInstalled` 是**内容记录**——取消订阅后残留的目录正属于这一类，所以它**不参与**订阅判断。程序只读这个文件，**清理与浏览都不需要 Steam 正在运行**（只有面板里的取消订阅/重新订阅需要，见「取消订阅与重新订阅」） |
| 目录自身的修改时间 | 兜底：30 分钟内被改动过的目录先不删，避免碰到还在下载的壁纸（Steam 记录已写明内容装完的不受此限） |

Steam 的这份记录只在**它比 WE 缓存新**时才被采信（谁更新就信谁）：

- **刚下载完**：Steam 在下载完成时立刻写下记录，而 Wallpaper Engine 要等它自己刷新工坊列表才更新缓存，实测可能相差几十分钟。这时 Steam 记录更新，用它兜住刚下载的壁纸，不会显示成「已取消订阅」。
- **刚取消订阅**：WE 缓存已经把它删掉，但 Steam 还没重写文件、记录里仍写着 `subscribedby`。这时缓存更新，以缓存为准，残留立刻会被列为待清理。

这些额外来源只用于**保护**：待清理名单始终由订阅列表（WE 缓存）决定，Steam 记录只能让目录被保留，不会让任何目录变成待清理。读不到（文件不存在、格式变化、Steam 正在重写）就静默降级为只看缓存。

## 管理面板

```bash
python wallpaper-cleaner.py --web        # 浏览器模式
python wallpaper-cleaner.py --desktop    # 桌面窗口模式（需 pywebview）
```

浏览器模式下会自动打开 `http://127.0.0.1:8787/`，只监听本机回环地址，其他设备无法访问。

打开面板会**自动检查一遍并勾选好可清理的残留**，确认无误后直接点「清理选中」即可，不需要自己找按钮。

### 面板能做什么

- **概览**：已订阅壁纸、磁盘文件夹、待清理、可释放空间
- **待清理列表**：列出所有不在订阅列表中的文件夹，带标题、类型与预览缩略图（点图可放大）。打开时已自动勾选「已取消订阅」的部分，想保留哪个取消勾选即可；点「占用空间」列头可按大小切换排序
- **点标题打开目录**：两张列表的标题都可以点，用系统文件管理器（Windows 资源管理器）打开这张壁纸所在的文件夹，方便删除前先看一眼里面到底是什么；标题前面有一个文件夹图标，平时还带一条很淡的点状下划线（悬停变实线），没有标题的行显示 `—`，不可点
- **删除到回收站**：默认把文件夹移入回收站而不是永久删除，误操作可以从回收站恢复
- **已订阅列表**：查看订阅中的壁纸标题、预览图、标注大小与实际占用，支持按 ID / 标题筛选，点「实际占用」列头可按大小排序；勾选后可以批量取消订阅（见下）
- **重新订阅**：在待清理列表里勾选残留的壁纸，点「重新订阅选中」，Steam 会把它们重新订阅并下载回本地
- **Steam 状态徽标**：顶栏常驻显示 Steam 是否可用（可用时还会显示你的订阅数量），不可用时点一下就能看到原因与下一步
- **高级**：位置设置（含一键自动检测，保存时会保留配置文件里的注释）、运行日志、版本与数据目录

两张表的「占用大小」列头都可以点，排序在 默认（不干预后端顺序：待清理本来就是大到小、已订阅是 ID 升序）→ 大到小（▼）→ 小到大（▲）之间循环。两张表各排各的，互不影响；重扫与勾选引起的重绘也不会把排序选择弄丢。

清理前会弹出确认框，逐条列出将要清理的内容与总大小。

### 取消订阅与重新订阅

这两项操作走 Steam 官方 Steamworks 接口（`ISteamUGC`），效果等同于你在 Steam 客户端里点一下，因此**需要 Steam 正在运行且已登录**。工具不接触你的账号密码，也不修改任何游戏文件。

**取消订阅**：在「已订阅壁纸」列表里勾选，点「取消订阅选中」，确认框里二选一：

| 方式 | 含义 |
|------|------|
| **仅取消订阅** | 本工具不主动删本地文件。Steam 客户端通常会在取消订阅后自行删除它们；如果留下来了，下次扫描会显示成「已取消订阅」，按残留清理即可 |
| **取消订阅并删除文件** | 取消订阅成功后由本工具再清理一次目录（默认回收站，可反选为永久删除），确保空间立刻释放。删除后本工具无法一键找回，需要到创意工坊重新订阅 |

无论哪种方式，工具都会在取消订阅后向 Steam 确认订阅确实取消了；只提交成功、迟迟没确认的会单独提示，并且**不会**去删本地的文件（那时它可能仍是订阅状态，删了会被重新下载）。

**重新订阅**：在待清理列表里勾选残留（只有纯数字 ID 的条目可以，来源不明的文件夹没有可用的 workshop ID），点「重新订阅选中」，Steam 会自动下载。文件还在本地的通常不必整包重下。

**关于 Steam 连接**：面板打开时会自动检测一次，顶栏徽标给出结论——

| 徽标 | 含义 |
|------|------|
| Steam 未检测 · 检测（灰） | 还没检测过，点一下即可检测 |
| 正在检测 Steam… | 检测进行中（在后台，不会挡住扫描） |
| Steam 可用 · N 个订阅（绿） | 已连上，N 就是你这个账号当前的订阅数量 |
| Steam 不可用 · 查看原因（黄） | 点开有具体原因与下一步：启动 Steam、重新检测，或者直接在 Steam 里手动操作 |

连不上时不会只甩一句报错：点「取消订阅选中」会弹出引导，告诉你当前是什么问题（Steam 没运行 / 没登录 / 找不到可用的 `steam_api64.dll` 等）、该怎么办，并提供一个「在 Steam 中手动操作」的跳转作为保底。

它用的是本机现成的 `steam_api64.dll`：优先用 Wallpaper Engine 自带的那份（同产品、同 AppID，且目录里已经有正确的 `steam_appid.txt`，**不写任何文件**），找不到时才退而用其它已安装游戏自带的，最后才在程序自己的数据目录下写一份 `steam_ctx/steam_appid.txt`。**永远不会往第三方游戏目录写东西**；自动查找不理想时可以在配置文件里用 `steam_dll_path` 指定。

> 需要知道的两点：以 Wallpaper Engine 的 AppID 连接 Steam 期间，Steam 会认为「Wallpaper Engine 正在运行」（好友状态、库页面），这是 Steamworks 的固有行为，因此工具**用完即断**，不留常驻连接；此外「借用已安装游戏的 dll 连接 Steam」是社区通行做法，但不是 Valve 文档化的用法，仅供个人使用、风险自担。

### 目录分类

在交叉核对（Steam 订阅记录）与新鲜度保护之后，剩余的文件夹分成两类，避免误删：

| 类型 | 判定 | 默认 |
|------|------|------|
| **已取消订阅** | 目录名是纯数字（符合 workshop ID 格式） | 打开面板时自动勾选 |
| **无法确定的文件夹** | 目录名不是纯数字，来源不明（如手动备份） | 永远不自动勾选，必须逐个手动勾选 |

两类都会显示壁纸自己的标题与类型：它们来自目录里的 `project.json`（作者随内容一起发布的名字），所以即使已经取消订阅、WE 缓存里查不到，也认得出是哪个壁纸；读不到时该列显示 `—`。面板的待清理表格、清理确认框和命令行 `--dry-run` 预览都用这个名字。

### 缩略图从哪来

表格最左边一列是壁纸的预览图，用的就是作者随内容一起发布、已经躺在壁纸目录里的那张图（`project.json` 的 `preview` 字段，通常是 `preview.jpg` 或动图 `preview.gif`；字段缺失或不可信时按 `preview.jpg` → `preview.png` → `preview.jpeg` → `preview.gif` 依次找）。面板只把这张图的字节发出去，解码、缩放、动图播放都由浏览器完成，所以：

- 不联网、不问 Steam 要图，也不新增任何依赖（仍是纯标准库）
- 动图在列表里是**真的会动**的，点一下还能放大看原图
- 目录里没有预览图时显示一个虚线占位框，不会去猜、也不会去解码视频

接口 `GET /api/thumb?wid=<ID>` 只认最近一次扫描结果里的目录：ID 与文件名都来自扫描本身，不接受请求里带来的路径，读之前还会再校验一次文件确实在壁纸目录内、是图片、且不超过 8 MB。

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--desktop` | 启动桌面窗口（需要 pywebview） |
| `--web` | 启动浏览器管理面板 |
| `--port` | 监听端口。桌面窗口默认由系统分配空闲端口，浏览器模式默认 `8787` |
| `--host` | 监听地址，默认 `127.0.0.1`（仅本机可访问） |
| `--no-browser` | 浏览器模式下不自动打开浏览器 |
| `--dry-run` | 只预览将要删除的内容，不实际删除 |
| `--check-deps` | 检查桌面窗口所需的依赖是否齐全，然后退出 |
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

不带参数时命令行会**立即删除**所有未订阅的文件夹（包括名称不是纯数字的那些），不提供预览。想先看清楚将删除什么，请用 `--dry-run` 或管理面板。

### 配置文件 (`config.yml`)

配置采用 YAML 格式，支持用 `#` 编写注释：

```yaml
# Wallpaper Engine 的 workshop 订阅缓存文件路径
json_path: D:\Steam\steamapps\common\wallpaper_engine\bin\workshopcache.json

# workshop 壁纸内容存放目录
workshop_dir: D:\Steam\steamapps\workshop\content\431960

# 取消订阅/重新订阅用的 steam_api64.dll（可填文件或所在目录），留空则自动查找
steam_dll_path: ""
```

| 字段 | 说明 |
|------|------|
| `json_path` | Wallpaper Engine 的 workshop 缓存文件路径 |
| `workshop_dir` | workshop 壁纸内容存放目录 |
| `steam_dll_path` | 可选。取消订阅/重新订阅用的 `steam_api64.dll`，可填文件或所在的目录；留空则自动查找（优先用 Wallpaper Engine 自带的那份） |

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
  steamapi.py                   # 连接运行中的 Steam（Steamworks ISteamUGC），订阅/取消订阅
  cli.py                        # 命令行流程
  web.py                        # 面板服务端（标准库 http.server）
  desktop.py                    # 桌面窗口（WebView2）、单实例守卫、控制台接管
  static/                       # 面板前端（原生 HTML/CSS/JS，无构建步骤）
packaging/
  wallpaper-cleaner.spec        # PyInstaller 打包配置
  make_icon.py                  # 程序图标（纯标准库画出来的，含 favicon 同款造型）
  build.py                      # 一键构建 exe
  smoke_test.py                 # 打包产物冒烟测试
tests/test_core.py              # 单元测试
tests/test_web.py               # 面板接口与删除守卫的单元测试
tests/test_steamapi.py          # Steam 连接层的单元测试（用假 dll，全程离线）
```

## 日志

每次运行会在 `logs/` 目录下生成日志文件，记录删除和保留的详细信息。日志按天分文件，单文件最大 5MB，保留 5 个备份。

## 安全提示

- 程序仅对比订阅列表、Steam 的订阅记录与本地文件，不会删除已订阅的壁纸
- 待清理名单始终由订阅列表（WE 缓存）决定，Steam 记录只能让目录被保留，不会让任何目录变成待清理
- 删除前会重新读一次订阅缓存与 Steam 记录：仍处于订阅状态的会被自动跳过，并在日志里说明原因
- 删除前还会跳过 30 分钟内被改动过的目录，避免删到正在下载的壁纸；Steam 内容记录里已写明装完的不受此限（该记录是否比 WE 缓存旧无关紧要，「装完」是过去发生的事，不会因为缓存被重写而失效）（面板会提示「跳过」，日志可查）
- 订阅缓存缺 `wallpapers` 字段时（Wallpaper Engine 正在重写）会直接报错中止，而不是把所有目录当成残留
- 管理面板的删除操作有三层校验：路径必须是 `workshop_dir` 的直接子目录、必须是最近一次扫描确认过的待清理项、必须携带本次启动的访问令牌
- 取消订阅只作用于最近一次扫描确认「仍在订阅」的壁纸，重新订阅只作用于最近一次扫描到的残留目录，两者同样要求访问令牌与同一次扫描的 `scan_id`；执行前还会拿运行中的 Steam 的真实订阅列表再核对一遍，扫描之后被订阅/取消订阅过的会被跳过
- 刚在本面板里重新订阅过的目录会被删除流程自动跳过（本地订阅记录刷新有延迟，这段时间只有面板自己知道它已经回到订阅列表）
- 取消订阅与重新订阅作用于你的 **Steam 账号**（等价于在客户端里点一下），可以随时在 Steam 客户端里改回来；执行时使用账号已登录的客户端会话，**不接触账号密码、不注入、不 hook、不修改游戏文件**
- 程序只写自己的东西：配置（`config.yml`）与日志（`logs/`）；自动查找 `steam_api64.dll` 时不会往任何第三方游戏目录写文件，只有在 dll 同目录没有可用的 appid 文件时才在程序自己的数据目录下写一份 `steam_ctx/steam_appid.txt`。**始终不写** Steam 的 ACF、Wallpaper Engine 的 `workshopcache.json` 与 WE 自己的配置
- 缩略图接口是只读的，同样只认最近一次扫描结果里的目录，不接受请求里带来的路径（见「缩略图从哪来」）
- 「点标题打开目录」也要带访问令牌（走 POST，跨站页面发不出这种请求），同样只打开最近一次扫描结果里的目录，且只把目录交给系统去打开，不读、不改目录里的任何文件
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

只重新打包（依赖已就绪，仍会优先使用 `.venv-build`）：

```bash
python packaging/build.py --skip-deps
```

### 换程序图标

图标是用 `packaging/make_icon.py` **画出来的**，不是仓库里的二进制文件，构建时由 spec 生成（和版本信息同样做法）。改造型或调色只改这个脚本：

```bash
python packaging/make_icon.py --preview          # 生成图标，并输出各尺寸与 16px 放大对比图
python packaging/make_icon.py --design c         # 换造型
```

可选的造型见脚本里的 `DESIGNS`。任务栏和资源管理器小图标用的是 16px，那个尺寸下认不出来就等于没有图标，所以每个造型都标了实测的可辨认度 —— 改完务必用 `--preview` 出的 16px 放大图确认一眼。换完重新跑 `python packaging/build.py` 即可。

浏览器标签页的图标是 `index.html` 里内联的 SVG（同样造型），换造型时一并改。

### 发版流程

1. 更新 `wallpaper_cleaner/__init__.py` 里的 `__version__`
2. 提交并推送分支：`git push origin main`
3. 打标签并推送：`git tag -a v0.1.0 -m "v0.1.0" && git push origin v0.1.0`
4. GitHub Actions 会自动跑测试、校验标签与版本号一致、打包、冒烟测试，然后创建 Release 并把 exe 和 `SHA256SUMS.txt` 传上去

标签和代码里的版本号不一致时构建会直接失败，避免发出版本号对不上的包。工作流也支持手动触发，此时只构建并上传 artifact，不发布 Release。

### 代码签名

当前 exe 未签名，用户首次运行会遇到 SmartScreen 提示，绕过方法见本文开头。消除它需要一份代码签名证书；`.github/workflows/release.yml` 里预留了插入签名步骤的位置（必须在生成 `SHA256SUMS.txt` 之前完成签名，否则校验和对不上）。

## 许可协议

采用 [MIT License](LICENSE)，版权归 halfaradish 所有。

桌面窗口模式依赖 pywebview（BSD-3-Clause）、pythonnet 与 clr_loader（MIT），命令行与浏览器面板模式只用 Python 标准库。这些依赖的许可均为宽松许可，与本项目协议兼容。

「借本机已安装游戏的 `steam_api64.dll` 连接 Steam 客户端」这一思路参考了 [xiaoyuyu6420/wallpaper-engine-cleaner](https://github.com/xiaoyuyu6420/wallpaper-engine-cleaner)（MIT）；本项目的 `steamapi.py` 是独立实现（改用 Wallpaper Engine 自带的 dll、加锁串行化调用、以轮询订阅列表确认结果），未复制其代码。
