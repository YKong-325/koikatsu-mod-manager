(() => {
  const $ = id => document.getElementById(id);
  // "modtrash" is the folder that holds delete-log.jsonl. Deleting a Mod only
  // renames it in place, so the folder is no longer a place files go and the
  // form shows it read-only: moving it would orphan the restore history.
  // "archive" is the opposite: it does hold the moved packages, and its log
  // lives inside it, so it can be pointed anywhere the game does not scan.
  const fields = ['game', 'mods', 'chara', 'coordinate', 'studio', 'modtrash',
    'archive'];
  const optional = ['chara', 'coordinate', 'studio'];
  let token = '', busy = true, userdata = '', loadedGame = '';
  function status(message, error = false) {
    $('status').textContent = message;
    $('status').classList.toggle('error', error);
  }
  function updateControls() {
    document.querySelectorAll('input,button').forEach(node => { node.disabled = busy; });
    optional.forEach(key => {
      $(key).disabled = busy || !$('enable-' + key).checked;
      document.querySelector(`[data-browse="${key}"]`).disabled = $(key).disabled;
    });
  }
  async function post(endpoint, data) {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Catalog-Token': token},
      body: JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '操作失败');
    return result;
  }
  function fillDefaults() {
    const game = $('game').value.trim().replace(/[\\/]+$/, '');
    if (!game) { $('game').focus(); return; }
    const separator = game.includes('\\') ? '\\' : '/';
    const join = (...parts) => [game, ...parts].join(separator);
    userdata = join('UserData');
    $('mods').value = join('mods');
    optional.forEach(key => {
      $(key).value = join('UserData', key);
      $('enable-' + key).checked = true;
    });
    $('modtrash').value = join('modtrash');
    $('archive').value = join('mods_archive');
    updateControls();
  }
  $('defaults').onclick = fillDefaults;
  optional.forEach(key => { $('enable-' + key).onchange = updateControls; });
  document.querySelectorAll('[data-browse]').forEach(button => {
    button.onclick = async () => {
      const key = button.dataset.browse;
      busy = true; updateControls(); status('请选择文件夹…');
      try {
        const result = await post('/api/pick-folder', {initial: $(key).value || $('game').value});
        if (result.path) {
          $(key).value = result.path;
          if (key === 'game') fillDefaults();
        }
        status('');
      } catch (error) { status(error.message, true); }
      finally { busy = false; updateControls(); }
    };
  });
  $('settings').onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    const values = Object.fromEntries(fields.map(key => [key, $(key).value.trim()]));
    optional.forEach(key => { if (!$('enable-' + key).checked) values[key] = ''; });
    const separator = values.game.includes('\\') ? '\\' : '/';
    values.userdata = values.game === loadedGame ? userdata :
      values.game.replace(/[\\/]+$/, '') + separator + 'UserData';
    busy = true; updateControls(); status('正在建立索引…');
    $('save').textContent = '正在建立索引…';
    let polling = false, progressActive = true;
    const progressTimer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        const response = await fetch('/api/progress');
        if (response.ok) {
          const progress = await response.json();
          if (progressActive) status(progress.message || '正在建立索引…');
        }
      } catch {}
      finally { polling = false; }
    }, 1000);
    try {
      await post('/api/config', values);
      location.replace('/gallery.html');
    } catch (error) { status(error.message, true); }
    finally { progressActive = false; clearInterval(progressTimer); busy = false; $('save').textContent = '保存并建立索引'; updateControls(); }
  };
  async function load() {
    try {
      const session = await fetch('/api/session');
      if (!session.ok) throw new Error('无法连接管理器');
      token = (await session.json()).token;
      const response = await fetch('/api/config');
      if (!response.ok) throw new Error('无法读取目录配置');
      const result = await response.json();
      fields.forEach(key => { $(key).value = result.values[key] || ''; });
      userdata = result.values.userdata || '';
      loadedGame = result.values.game || '';
      optional.forEach(key => { $('enable-' + key).checked = Boolean(result.values[key]); });
      $('back').hidden = !result.ready;
      status(result.error || '', Boolean(result.error));
      busy = false; updateControls();
    } catch (error) { status(error.message, true); }
  }
  updateControls(); load();
})();
