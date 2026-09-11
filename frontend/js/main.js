// 第 4 块：页面启动入口——等 i18n 就绪后拉一次配置/状态，语言切换时重渲染动态文案。

i18n.ready.then(function () {
  populateTargetLanguageOptions();
  $('cfg-target-language').value = 'Simplified Chinese';
  loadConfig();
  refreshStatus();
});
i18n.onChange(function () {
  // 语言切换后重新渲染依赖 i18n.t() 拼出的动态文案（data-i18n 静态部分由 i18n.js 自己刷新）
  bridge.fetch('status').then(function (resp) {
    if (resp.err === 'ok') renderJob(resp.data);
  }).catch(function () {});
});
