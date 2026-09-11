# wallpaper-cleaner

自动清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件，释放存储空间。

## 环境要求

- Windows 系统（Steam 路径自动检测依赖 Windows 注册表）
- Python 3.6 及以上版本，无需安装任何第三方依赖
- 已安装 Steam 和 Wallpaper Engine

## 工作原理

1. 读取 Wallpaper Engine 的 `workshopcache.json`，获取当前所有已订阅壁纸的 workshop ID
2. 遍历 workshop 内容目录，对比本地文件夹与订阅列表
3. 删除不在订阅列表中的文件夹，保留已订阅的壁纸

## 使用方法

### 首次运行

直接运行脚本，程序会自动检测 Wallpaper Engine 路径：

```bash
python wallpaper-cleaner.py
```

- **自动检测成功**：展示检测到的路径，确认后直接执行清理
- **自动检测失败**：自动生成 `config.yml`，按提示编辑配置文件后重新运行

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

## 日志

每次运行会在 `logs/` 目录下生成日志文件，记录删除和保留的详细信息。

## 安全提示

- 程序仅对比订阅列表与本地文件，不会误删已订阅壁纸
- 建议首次运行前备份重要壁纸文件
