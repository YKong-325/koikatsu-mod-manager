(() => {
  const host = document.querySelector('.tabs') || document.querySelector('header') ||
    document.querySelector('.startup-content');
  if (!host) return;
  const quit = document.createElement('button');
  quit.type = 'button'; quit.className = 'quit-manager';
  quit.textContent = '退出管理器';
  quit.onclick = async () => {
    quit.disabled = true;
    try {
      const session = await fetch('/api/session');
      if (!session.ok) throw new Error('无法连接管理器');
      const {token} = await session.json();
      const response = await fetch('/api/shutdown', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Catalog-Token': token},
        body: '{}'
      });
      if (!response.ok) throw new Error('退出失败，请稍后重试');
      document.body.replaceChildren();
      document.body.className = 'startup-page';
      const message = document.createElement('p');
      message.className = 'startup-content';
      message.textContent = '管理器已退出，可以关闭此页面。';
      document.body.append(message);
    } catch (error) {
      quit.disabled = false;
      const status = document.getElementById('status');
      if (status) status.textContent = error.message;
    }
  };
  host.append(quit);
})();
