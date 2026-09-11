// 全局运行时 + 跨模块共用的一点小工具。
//
// 这个页面没有打包器，所有 <script> 标签共用同一个全局作用域，靠加载顺序保证依赖关系：
// state.js 必须排在 config.js / source.js / task.js / main.js 之前。

var bridge = window.MyBooksToolBridge;
var i18n = window.MyBooksToolI18n.create();

function applyTheme(theme) { document.body.setAttribute('data-theme', theme); }
applyTheme(bridge.theme);
bridge.onThemeChange(applyTheme);

var state = {
  hasApiKey: false,
  source: null,          // { bookId, title }
  currentJobId: null,
  pollTimer: null,
};

function $(id) { return document.getElementById(id); }

function showAlert(el, message, level) {
  el.textContent = message;
  el.className = 'mb-alert hint mb-alert--' + (level || 'info');
  el.hidden = false;
}
function hideAlert(el) { el.hidden = true; }
