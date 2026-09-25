# wallpaper-cleaner

自动清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件，释放存储空间。

提供两种使用方式：**命令行**（一键清理）和**浏览器管理面板**（先预览再勾选删除）。

## 环境要求

- Windows 系统（Steam 路径自动检测依赖 Windows 注册表）
- Python 3.6 及以上版本，无需安装任何第三方依赖
- 已安装 Steam 和 Wallpaper Engine

## 工作原理

1. 读取 Wallpaper Engine 的 `workshopcache.json`，获取当前所有已订阅壁纸的 workshop ID
2. 遍历 workshop 内容目录，对比本地文件夹与订阅列表
3. 删除不在订阅列表中的文件夹，保留已订阅的壁纸

## 管理面板（推荐）

```bash
python wallpaper-cleaner.py --web
```

浏览器会自动打开 `http://127.0.0.1:8787/`。面板只监听本机回环地址，其他设备无法访问。

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
| `--web` | 启动管理面板 |
| `--port` | 面板端口，默认 `8787`；被占用时自动向后递增 |
| `--host` | 面板监听地址，默认 `127.0.0.1`（仅本机可访问） |
| `--no-browser` | 启动面板后不自动打开浏览器 |
| `--dry-run` | 只预览将要删除的内容，不实际删除 |

```bash
python wallpaper-cleaner.py --web --port 9000 --no-browser
python wallpaper-cleaner.py --dry-run          # 命令行预览模式
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

路径支持绝对路径和相对路径（相对路径基于脚本所在目录）。Windows 路径可不加引号直接书写（引号内的内容不会被转义处理）。

> 兼容说明：旧版 `config.json` 仍会被自动读取；重命名为 `config.yml` 后即可使用注释功能。

## 目录结构

```
wallpaper-cleaner.py            # 入口：无参数走命令行，--web 启动面板
wallpaper_cleaner/
  core.py                       # 路径检测、配置读写、扫描、删除（纯逻辑，不打印不退出）
  cli.py                        # 命令行流程
  web.py                        # 面板服务端（标准库 http.server）
  static/                       # 面板前端（原生 HTML/CSS/JS，无构建步骤）
tests/test_core.py              # 单元测试
```

## 日志

每次运行会在 `logs/` 目录下生成日志文件，记录删除和保留的详细信息。日志按天分文件，单文件最大 5MB，保留 5 个备份。

## 安全提示

- 程序仅对比订阅列表与本地文件，不会删除已订阅的壁纸
- 删除前会重新读取一次订阅列表：若某个壁纸在扫描后又被重新订阅，会被自动跳过
- 管理面板的删除操作有三层校验：路径必须是 `workshop_dir` 的直接子目录、必须是最近一次扫描确认过的待清理项、必须携带本次启动的访问令牌
- 建议首次运行前备份重要壁纸文件

## 开发

运行单元测试（全部在临时目录沙箱内，不会触碰真实配置与真实 Steam 目录）：

```bash
python -m unittest discover -s tests -v
```
