// 第 2 块：选书——搜索书库、渲染列表、记录当前选中的来源。

function renderBookList(books) {
  var container = $('book-list');
  container.innerHTML = '';
  if (!books.length) {
    var empty = document.createElement('p');
    empty.className = 'mb-text-secondary';
    empty.textContent = i18n.t('source.search.empty');
    container.appendChild(empty);
    return;
  }
  books.forEach(function (book) {
    var item = document.createElement('div');
    item.className = 'mb-list-item';

    // 左侧封面
    var coverHtml = '';
    if (book.thumb) {
      coverHtml = '<div class="mb-list-item__cover"><img src="' + book.thumb + '" alt="" onerror="this.style.display=\'none\'" /></div>';
    }

    item.innerHTML =
      coverHtml +
      '<div class="mb-list-item__content">' +
      '<div class="mb-list-item__title"></div>' +
      '<div class="mb-list-item__subtitle"></div>' +
      '</div>';
    item.querySelector('.mb-list-item__title').textContent = book.title || ('#' + book.book_id);
    item.querySelector('.mb-list-item__subtitle').textContent = (book.authors || []).join(', ');
    item.addEventListener('click', function () {
      container.querySelectorAll('.mb-list-item.selected').forEach(function (el) {
        el.classList.remove('selected');
      });
      item.classList.add('selected');
      state.source = { bookId: book.book_id, title: book.title || ('#' + book.book_id) };
      updateSelectedSourceHint();
    });
    container.appendChild(item);
  });
}

function searchBooks() {
  var q = $('book-search').value.trim();
  bridge.fetch('books?q=' + encodeURIComponent(q) + '&limit=20').then(function (resp) {
    if (resp.err !== 'ok') throw new Error(resp.msg || resp.err);
    renderBookList(resp.data);
  }).catch(function (err) {
    $('book-list').innerHTML = '';
    showAlert($('cfg-alert'), i18n.t('source.search.failed', { error: err.message || err }), 'error');
  });
}
$('book-search-btn').addEventListener('click', searchBooks);
$('book-search').addEventListener('keydown', function (e) {
  if (e.key === 'Enter') searchBooks();
});

function updateSelectedSourceHint() {
  var el = $('source-selected');
  if (!state.source) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = i18n.t('source.selected', { title: state.source.title });
}
