// 弹窗登记表
//
// 每个弹窗在这里说明两件事：关闭按钮是哪个、以及什么情况下不许关。
// 其余（焦点陷阱、Esc、背景 inert、遮罩点击）由 ui/overlay.js 与原生 <dialog> 负责。
import { $ } from '../core/dom.js';
import { closeDialog, initDialogs, registerDialog } from '../ui/overlay.js';
import { progressDialogClosable } from '../ui/progress.js';

export function initDialogRegistry() {
  initDialogs();

  // 进度弹窗：任务跑着的时候不许关（关掉窗口会中断删除，留下半残目录），
  // 只有从状态栏手动点开的那种才允许"在后台继续"
  registerDialog('dlg-progress', {
    dismissible: false,
    canClose: progressDialogClosable,
  });

  // 三个确认框都不允许点遮罩关闭：它们的前一步是用户明确点了"清理选中"，
  // 误触遮罩把确认框关掉会让人以为操作已经取消了
  registerDialog('dlg-confirm', { dismissible: false });
  registerDialog('dlg-unsub', { dismissible: false });
  registerDialog('dlg-resub', { dismissible: false });

  // 这几个是纯信息展示，怎么关都行
  registerDialog('dlg-steam', {});
  registerDialog('dlg-help', {});
  registerDialog('dlg-update', {});

  const bind = (id, dialogId) => {
    const el = $(id);
    if (el) el.addEventListener('click', () => closeDialog(dialogId));
  };

  bind('btn-close-help', 'dlg-help');
  bind('btn-update-later', 'dlg-update');
}
