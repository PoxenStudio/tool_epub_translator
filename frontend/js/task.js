// 第 3 块：输出方式、启动翻译、任务状态轮询与渲染、结果展示。

document.querySelectorAll('input[name="output-mode"]').forEach(function (radio) {
  radio.addEventListener('change', function () {
    // 触发 change 的这个 radio 就是刚被选中的那个，不用再重新查一次 :checked
    // （之前这里误用了按 id 查找的 $()，选择器字符串当 id 用必然查不到，抛 null.value）。
    $('new-book-title-field').hidden = (radio.value !== 'new_book');
  });
});
function getOutputMode() {
  var checked = document.querySelector('input[name="output-mode"]:checked');
  return checked ? checked.value : 'download';
}

// ---------- 启动翻译 ----------

function startTranslate() {
  if (!state.source) {
    showAlert($('start-alert'), i18n.t('start.needSource'), 'warning');
    return;
  }
  var outputMode = getOutputMode();
  var title = $('new-book-title').value.trim() || null;
  hideAlert($('start-alert'));
  // 清理上一次任务残留的错误信息
  $('task-error-alert').hidden = true;
  $('task-error-alert').textContent = '';
  $('result-expired-panel').hidden = true;

  var request = bridge.fetch('translate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ book_id: state.source.bookId, output_mode: outputMode, title: title }),
  });

  request.then(function (resp) {
    if (resp.err !== 'ok') throw new Error(resp.msg || resp.err);
    renderJob(resp.data);
    startPolling();
  }).catch(function (err) {
    showAlert($('start-alert'), i18n.t('start.failed', { error: err.message || err }), 'error');
  });
}
$('start-btn').addEventListener('click', startTranslate);

// ---------- 任务状态轮询 ----------

function refreshStatus() {
  return bridge.fetch('status').then(function (resp) {
    if (resp.err !== 'ok') throw new Error(resp.msg || resp.err);
    renderJob(resp.data);
  }).catch(function () { /* 静默失败，下一轮再试 */ });
}

function startPolling() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(refreshStatus, 1500);
}
function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function renderJob(job) {
  if (!job || job.status === 'idle') {
    $('task-empty').hidden = false;
    $('task-panel').hidden = true;
    $('result-section').hidden = true;
    $('start-btn').disabled = false;
    stopPolling();
    return;
  }

  state.currentJobId = job.job_id;
  $('task-empty').hidden = true;
  $('task-panel').hidden = false;
  $('task-book').textContent = i18n.t('task.book', { title: job.book_title || '' });
  $('task-stage').textContent = i18n.t('task.stage.' + (job.stage || 'queued')) || job.stage;
  $('task-progress-bar').style.width = (job.progress || 0) + '%';

  var tokens = job.tokens || {};
  $('token-input').textContent = tokens.input != null ? tokens.input : '–';
  $('token-cache').textContent = tokens.input_cache != null ? tokens.input_cache : '–';
  $('token-output').textContent = tokens.output != null ? tokens.output : '–';
  $('token-total').textContent = tokens.total != null ? tokens.total : '–';
  $('token-unavailable').hidden = !!tokens.available;

  var errorAlert = $('task-error-alert');
  // interrupted：服务重启时发现的"假运行中"任务（epub_translator 不支持断点续跑，
  // 见 job_store.py），跟 failed 一样是终态，只是错误原因不同，界面上一并当失败展示。
  if ((job.status === 'failed' || job.status === 'interrupted') && job.error) {
    errorAlert.hidden = false;
    errorAlert.textContent = i18n.t('task.error', { error: job.error });
  } else {
    errorAlert.hidden = true;
  }

  var running = job.status === 'running';
  var cancelling = job.stage === 'cancelling';
  $('start-btn').disabled = running;
  // 只有运行中的任务能取消；epub_translator 没有真正的断点续跑能力（见 job_store.py），
  // 所以这里没有对应的"恢复"按钮——终态任务想重译，直接用上面的"开始翻译"整本重跑。
  var cancelBtn = $('task-cancel-btn');
  cancelBtn.hidden = !running;
  cancelBtn.disabled = cancelling;
  cancelBtn.textContent = i18n.t(cancelling ? 'action.cancelling' : 'action.cancel');
  if (running) {
    startPolling();
  } else {
    stopPolling();
  }

  renderResult(job);
}

// ---------- 取消任务 ----------

function cancelTask() {
  if (!state.currentJobId) return;
  var btn = $('task-cancel-btn');
  btn.disabled = true;
  btn.textContent = i18n.t('action.cancelling');
  bridge.fetch('cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: state.currentJobId }),
  }).then(function (resp) {
    if (resp.err !== 'ok') throw new Error(resp.msg || resp.err);
    renderJob(resp.data);
  }).catch(function (err) {
    var errorAlert = $('task-error-alert');
    errorAlert.hidden = false;
    errorAlert.textContent = i18n.t('task.error', { error: err.message || err });
    btn.disabled = false;
    btn.textContent = i18n.t('action.cancel');
  });
}
$('task-cancel-btn').addEventListener('click', cancelTask);

function renderResult(job) {
  var section = $('result-section');
  var downloadPanel = $('result-download-panel');
  var newBookPanel = $('result-newbook-panel');
  var expiredPanel = $('result-expired-panel');
  downloadPanel.hidden = true;
  newBookPanel.hidden = true;
  expiredPanel.hidden = true;
  $('result-downloaded-hint').hidden = true;

  if (job.status !== 'completed' && job.status !== 'failed') {
    section.hidden = true;
    return;
  }
  if (job.status === 'failed') {
    section.hidden = true; // 错误已经在任务区展示，这里不重复
    return;
  }

  section.hidden = false;
  if (job.output_mode === 'new_book') {
    if (job.new_book && job.new_book.book_id) {
      newBookPanel.hidden = false;
      $('result-newbook-title').textContent = job.new_book.title || '';
      $('result-newbook-link').setAttribute('href', job.new_book.url || '#');
    } else {
      section.hidden = true;
    }
  } else if (job.result_expired) {
    expiredPanel.hidden = false;
  } else if (job.result_ready) {
    downloadPanel.hidden = false;
    $('result-download-link').setAttribute(
      'href', '/api/toolbox/tool/' + bridge.toolId + '/result'
    );
  } else if (job.result_downloaded) {
    $('result-downloaded-hint').hidden = false;
  } else {
    section.hidden = true;
  }
}
