"""命令行流程：与旧版 wallpaper-cleaner.py 行为保持一致

差异仅在于：config 相关错误由 core 抛出异常，这里负责转成日志与退出码。
"""

import sys

from . import core

logger = core.logger


def _create_default_config_and_exit():
    """首次运行：生成默认配置并友好退出（退出码 0）"""
    logger.warning('未检测到配置文件，正在自动生成默认配置文件...')
    try:
        path = core.write_default_config()
    except core.ConfigError as e:
        logger.error(str(e))
        sys.exit(1)

    logger.info(f'默认配置文件已生成: {path}')
    logger.info('')
    logger.info('=' * 60)
    logger.info('  请先编辑配置文件，填入正确的路径信息：')
    logger.info(f'  配置文件位置: {path}')
    logger.info('  编辑完成后，重新运行本程序即可。')
    logger.info('=' * 60)
    sys.exit(0)


def _resolve_paths():
    """自动检测 → 用户确认 → 回退配置文件，返回 (json_path, workshop_dir)"""
    json_path, workshop_dir = None, None

    auto_json, auto_workshop = core.auto_detect_paths()
    if auto_json and auto_workshop:
        logger.info('自动检测到 Wallpaper Engine 路径:')
        logger.info(f'  workshopcache 文件: {auto_json}')
        logger.info(f'  workshop 内容目录: {auto_workshop}')
        logger.info('')
        user_input = core.ask_yes_no('是否使用自动检测的路径? (y/n，默认 y): ', '(y/n, default y): ')
        if user_input in ('', 'y', 'yes'):
            json_path, workshop_dir = auto_json, auto_workshop
            logger.info('已采用自动检测的路径')
        else:
            logger.info('用户选择不使用自动检测路径，回退到配置文件')
    else:
        logger.info('未能自动检测到 Wallpaper Engine 路径，回退到配置文件')

    if json_path and workshop_dir:
        return json_path, workshop_dir

    try:
        config = core.load_config()
    except core.ConfigMissingError:
        _create_default_config_and_exit()
    except core.ConfigError as e:
        logger.error(str(e))
        sys.exit(1)

    if config['is_legacy_json']:
        logger.info('已读取旧版 config.json；建议重命名为 config.yml，即可使用 # 注释')
    logger.info(f"配置加载成功: {config['config_file']}")
    return config['json_path'], config['workshop_dir']


def _preview(result, outcome=None):
    """--dry-run 输出：列出将要删除的内容"""
    logger.info('')
    logger.info('=' * 60)
    logger.info('  预览模式：以下内容不会被删除')
    logger.info('=' * 60)

    for item in result['orphans']:
        logger.info(f'  待删除 {item["wid"]}  ({core.format_size(item["size_bytes"])})')
    for item in result['unknown']:
        logger.info(f'  待删除 {item["wid"]}  ({core.format_size(item["size_bytes"])})　[非数字目录]')

    if not result['orphans'] and not result['unknown']:
        logger.info('  没有需要清理的内容')

    logger.info('')
    logger.info(f'待删除数量: {len(result["orphans"]) + len(result["unknown"])}')
    logger.info(f'可释放空间: {core.format_size(result["orphan_bytes"] + result["unknown_bytes"])}')
    logger.info('去掉 --dry-run 参数即可实际执行删除。')


def main(dry_run=False):
    core.setup_logger()
    logger.info('========== wallpaper-cleaner 开始执行 ==========')

    json_path, workshop_dir = _resolve_paths()

    try:
        subscriptions = core.load_subscriptions(json_path)
    except core.SubscriptionError as e:
        logger.error(str(e))
        sys.exit(1)
    logger.info(f'已读取订阅缓存: {json_path}')
    logger.info(f'已订阅壁纸数量: {len(subscriptions)}')

    try:
        result = core.scan(workshop_dir, subscriptions, json_path=json_path)
    except core.ScanError as e:
        logger.error(str(e))
        sys.exit(1)
    logger.info(f'workshop目录下文件夹总数: {result["total_folders"]}')

    for item in result['subscribed']:
        logger.debug(
            f'保留: {item["wid"]} | 标题: {item["title"]} | '
            f'标注大小: {item["declared_size"]} | '
            f'实际大小: {core.format_size(item["size_bytes"])}'
        )

    # CLI 与旧版一致：未订阅的目录一律删除（面板里未知目录默认不勾选，更保守）
    targets = result['orphans'] + result['unknown']

    if dry_run:
        _preview(result)
        return 0

    def _progress(_done, _total, message, level='info'):
        getattr(logger, level, logger.info)(message)

    outcome = core.delete_folders(
        targets, workshop_dir, to_recycle_bin=False, on_progress=_progress
    )

    logger.info('========== 执行完成 ==========')
    logger.info(f'扫描文件夹总数: {result["total_folders"]}')
    logger.info(f'保留文件夹数量: {len(result["subscribed"])}')
    logger.info(f'删除文件夹数量: {len(outcome["deleted"])}')
    logger.info(f'释放存储空间: {core.format_size(outcome["freed_bytes"])}')
    return 0
