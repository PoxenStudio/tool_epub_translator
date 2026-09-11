// 第 1 块：LLM 配置面板——加载/保存配置、测试连接。依赖 state.js 的 bridge/i18n/state/$。

// 与 epub_translator.translation.language 模块导出的常量值保持一致
var TARGET_LANGUAGES = [
  'Simplified Chinese', 'Traditional Chinese', 'English', 'Japanese', 'Korean',
  'Spanish', 'French', 'German', 'Portuguese', 'Russian', 'Italian', 'Arabic',
  'Hindi', 'Dutch', 'Polish', 'Turkish', 'Vietnamese', 'Thai', 'Indonesian',
  'Swedish', 'Danish', 'Norwegian', 'Finnish',
];

function populateTargetLanguageOptions() {
  var select = $('cfg-target-language');
  select.innerHTML = '';
  TARGET_LANGUAGES.forEach(function (lang) {
    var opt = document.createElement('option');
    opt.value = lang;
    opt.textContent = lang;
    select.appendChild(opt);
  });
}

function loadConfig() {
  bridge.fetch('config').then(function (resp) {
    if (resp.err !== 'ok') throw new Error(resp.msg || resp.err);
    var cfg = resp.data;
    $('cfg-api-url').value = cfg.api_url || '';
    $('cfg-model').value = cfg.model || '';
    $('cfg-token-encoding').value = cfg.token_encoding || 'o200k_base';
    $('cfg-target-language').value = cfg.target_language || 'Simplified Chinese';
    $('cfg-submit-kind').value = cfg.submit_kind || 'APPEND_BLOCK';
    $('cfg-user-prompt').value = cfg.user_prompt || '';
    state.hasApiKey = !!cfg.has_api_key;
    $('cfg-api-key').value = cfg.api_key || '';
    $('cfg-api-key').placeholder = i18n.t(
      state.hasApiKey ? 'config.apiKey.placeholderSet' : 'config.apiKey.placeholderUnset'
    );
  }).catch(function (err) {
    showAlert($('cfg-alert'), i18n.t('config.loadFailed', { error: err.message || err }), 'error');
  });
}

function saveConfig() {
  var payload = {
    api_url: $('cfg-api-url').value.trim(),
    model: $('cfg-model').value.trim(),
    token_encoding: $('cfg-token-encoding').value.trim(),
    target_language: $('cfg-target-language').value,
    submit_kind: $('cfg-submit-kind').value,
    user_prompt: $('cfg-user-prompt').value,
  };
  var apiKey = $('cfg-api-key').value.trim();
  if (apiKey) payload.api_key = apiKey;

  bridge.fetch('config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(function (resp) {
    if (resp.err !== 'ok') throw new Error(resp.msg || resp.err);
    state.hasApiKey = !!resp.data.has_api_key;
    $('cfg-api-key').value = resp.data.api_key || '';
    $('cfg-api-key').placeholder = i18n.t(
      state.hasApiKey ? 'config.apiKey.placeholderSet' : 'config.apiKey.placeholderUnset'
    );
    showAlert($('cfg-alert'), i18n.t('config.saved'), 'success');
    bridge.notify(i18n.t('config.saved'), 'success');
  }).catch(function (err) {
    showAlert($('cfg-alert'), i18n.t('config.saveFailed', { error: err.message || err }), 'error');
  });
}
$('cfg-save-btn').addEventListener('click', saveConfig);

function testConnection() {
  var apiUrl = $('cfg-api-url').value.trim();
  var apiKey = $('cfg-api-key').value.trim();
  var model = $('cfg-model').value.trim();

  if (!apiUrl || !model) {
    showAlert($('cfg-alert'), i18n.t('config.incomplete'), 'warning');
    return;
  }
  if (!apiKey && !state.hasApiKey) {
    showAlert($('cfg-alert'), i18n.t('config.incomplete'), 'warning');
    return;
  }

  var btn = $('cfg-test-btn');
  var saveBtn = $('cfg-save-btn');
  btn.disabled = true;
  saveBtn.disabled = true;
  btn.textContent = i18n.t('config.testing');
  hideAlert($('cfg-alert'));

  var payload = { api_url: apiUrl, model: model };
  if (apiKey) payload.api_key = apiKey;

  bridge.fetch('test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(function (resp) {
    if (resp.err === 'ok') {
      showAlert($('cfg-alert'), resp.msg || i18n.t('config.testSuccess'), 'success');
      bridge.notify(i18n.t('config.testSuccess'), 'success');
    } else {
      showAlert($('cfg-alert'), resp.msg || i18n.t('config.testFailed', { error: resp.err }), 'error');
    }
  }).catch(function (err) {
    showAlert($('cfg-alert'), i18n.t('config.testFailed', { error: err.message || err }), 'error');
  }).then(function () {
    btn.disabled = false;
    saveBtn.disabled = false;
    btn.textContent = i18n.t('config.test');
  });
}
$('cfg-test-btn').addEventListener('click', testConnection);
