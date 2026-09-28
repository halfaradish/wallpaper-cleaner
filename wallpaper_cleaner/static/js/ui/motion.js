// 动效系统：一处决定"动不动、动多快、能不能动"
//
// 每条动效都得说得出它为什么存在（层级 / 叙事 / 反馈 / 状态转换），说不出的不加。
// 这个模块负责的是那些 CSS 表达不了的：需要 JS 参与的计数、需要等动画结束才能继续的
// 离场、以及必须能被 reduced-motion 一刀切掉的那些。

const reducedQuery = window.matchMedia('(prefers-reduced-motion: reduce)');

export function prefersReduced() {
  return reducedQuery.matches;
}

// 计数滚动：统计数字变化时从旧值滚到新值。
// 理由是反馈——数字是这张表存在的理由，它变了就该被看见，而不是悄无声息地换掉。
// 首次渲染从 0 滚上去，这就是首屏那一下"数据落下来"的观感。
const counters = new WeakMap();
const COUNT_MS = 480;

export function countUp(el, to, format) {
  if (!el) return;
  const target = Number(to) || 0;
  const fmt = format || ((v) => String(Math.round(v)));
  const stored = el.dataset.countValue;
  const from = stored === undefined ? 0 : Number(stored) || 0;

  const running = counters.get(el);
  if (running) cancelAnimationFrame(running);
  counters.delete(el);

  // 页面不可见时直接落终值：隐藏的页面收不到 requestAnimationFrame 回调，
  // 走动画分支的话数字会一直停在旧值上——用户切回来看到的是一个错的数字，
  // 而这期间界面明明已经刷新过好几轮了。反正也没人看得见动画。
  if (prefersReduced() || from === target || document.hidden) {
    el.dataset.countValue = String(target);
    el.textContent = fmt(target);
    return;
  }

  el.dataset.countValue = String(target);
  const started = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - started) / COUNT_MS);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = fmt(t === 1 ? target : from + (target - from) * eased);
    if (t < 1) counters.set(el, requestAnimationFrame(step));
    else counters.delete(el);
  };
  counters.set(el, requestAnimationFrame(step));
}

// 整页交叉淡入：目前只用在主题切换上。
// 切换主题会让整屏颜色一起变，没有过渡时像是闪了一下；有了它才是"换了一层皮"。
//
// 关键是 mutate 必须保证被调用且只被调用一次：View Transition 的回调由浏览器决定
// 什么时候执行，页面不可见、上一个过渡还没结束等情况都可能让它迟迟不跑甚至不跑。
// 主题是用户点了就必须生效的东西，不能押在一个纯视觉效果上——所以这里加了兜底，
// 到点没执行就自己执行一次。
export function viewTransition(mutate) {
  if (prefersReduced() || typeof document.startViewTransition !== 'function') {
    mutate();
    return;
  }
  let applied = false;
  const once = () => {
    if (applied) return;
    applied = true;
    mutate();
  };
  try {
    document.startViewTransition(once);
  } catch (e) {
    // 上一个过渡还在跑时会抛，这时直接改就是了，不值得把主题卡住
    once();
    return;
  }
  setTimeout(once, 250);
}

// 让一批行先播完离场动画再重渲染：删掉的行直接消失的话，
// 用户没法确认"走的到底是哪几行"。
export function leaveThenRemove(rows, done) {
  const list = Array.from(rows || []).filter(Boolean);
  if (!list.length || prefersReduced()) {
    done();
    return;
  }
  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    done();
  };
  list[0].addEventListener('animationend', finish, { once: true });
  list.forEach((el) => el.classList.add('leaving'));
  // 兜底：动画被系统压掉、或者事件没送到时，不能让界面卡在"已经点过但什么都没发生"
  setTimeout(finish, 500);
}
