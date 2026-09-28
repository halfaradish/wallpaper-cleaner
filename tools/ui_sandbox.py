"""搭一个假的 Wallpaper Engine 环境，用来在真实浏览器里跑面板

不动真实配置：core.app_home_dir() 支持 WALLPAPER_CLEANER_HOME 覆盖，
把配置与日志都关进这个沙箱目录。

用法：python tools/ui_sandbox.py <沙箱目录>
"""
import json
import os
import struct
import sys
import zlib


def png(width, height, rgb):
    """生成一张纯色 PNG（不引第三方库，够浏览器渲染就行）"""
    raw = b''
    row = bytes(rgb) * width
    for _ in range(height):
        raw += b'\x00' + row  # 每行前面是 filter 字节

    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))


def make_folder(root, wid, title=None, preview=None, preview_bytes=None, extra_file=False):
    folder = os.path.join(root, wid)
    os.makedirs(folder, exist_ok=True)
    if title is not None:
        meta = {'title': title, 'type': 'scene'}
        if preview:
            meta['preview'] = preview
        with open(os.path.join(folder, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)
    if preview and preview_bytes:
        with open(os.path.join(folder, preview), 'wb') as f:
            f.write(preview_bytes)
    if extra_file:
        # 造点体积，让「磁盘占用」有值可看
        with open(os.path.join(folder, 'scene.pkg'), 'wb') as f:
            f.write(b'\x00' * (256 * 1024))
    return folder


def main():
    root = os.path.abspath(sys.argv[1])
    if os.path.exists(root):
        import shutil
        shutil.rmtree(root)

    workshop = os.path.join(root, 'steamapps', 'workshop', 'content', '431960')
    os.makedirs(workshop)

    blue = png(320, 180, (58, 111, 156))
    amber = png(320, 180, (217, 164, 65))
    green = png(320, 180, (79, 180, 119))

    # 订阅中、内容完整
    make_folder(workshop, '1111111111', '深海慢流', 'preview.png', blue, extra_file=True)
    make_folder(workshop, '2222222222', '城市夜行 · 霓虹雨', 'preview.png', amber)
    # 订阅中但本地没有目录（只在缓存里）→ 概览会提示"还没下载到本地"
    # 已取消订阅的残留：目录还在，内容完整
    make_folder(workshop, '3333333333', '森林晨雾', 'preview.png', green, extra_file=True)
    make_folder(workshop, '4444444444', '旧灯塔')
    # 内容已被 Steam 清理：连 project.json 都没有，只剩缓存文件
    make_folder(workshop, '5555555555')
    # 来源不明：目录名不是纯数字，永远不自动勾选
    make_folder(workshop, 'manual_backup', '手动备份的壁纸')

    cache = {
        'wallpapers': [
            {'workshopid': '1111111111', 'title': '深海慢流', 'filesizelabel': '312.4 MB'},
            {'workshopid': '2222222222', 'title': '城市夜行 · 霓虹雨', 'filesizelabel': '88.1 MB'},
            {'workshopid': '9999999999', 'title': '还没下载的那张', 'filesizelabel': '1.2 GB'},
        ]
    }
    cache_path = os.path.join(root, 'wallpaper_engine', 'bin', 'workshopcache.json')
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    with open(os.path.join(root, 'config.yml'), 'w', encoding='utf-8') as f:
        f.write('# 沙箱配置：指向假目录，绝不碰真实 Wallpaper Engine\n')
        f.write(f'json_path: {cache_path}\n')
        f.write(f'workshop_dir: {workshop}\n')

    print(root)


if __name__ == '__main__':
    main()
