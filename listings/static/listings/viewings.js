(() => {
  'use strict';
  document.querySelectorAll('[data-confirm]').forEach(button => button.addEventListener('click', event => {
    if (!window.confirm(button.dataset.confirm)) event.preventDefault();
  }));
  document.querySelectorAll('.vn-entry-form').forEach(form => {
    const kind = form.querySelector('[name="kind"]');
    const toggle = () => form.querySelectorAll('[data-visit-field]').forEach(field => {field.hidden = kind.value !== 'visit';});
    kind.addEventListener('change', toggle); toggle();
    const input = form.querySelector('[type="file"]');
    const preview = form.querySelector('[data-upload-preview]');
    let urls = [];
    input.addEventListener('change', () => {
      urls.forEach(url => URL.revokeObjectURL(url)); urls = []; preview.replaceChildren();
      Array.from(input.files).slice(0, 12).forEach(file => {
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return;
        const img = document.createElement('img'); const url = URL.createObjectURL(file);
        urls.push(url); img.src = url; img.alt = 'Выбранная фотография'; preview.append(img);
      });
    });
  });
  const dialog = document.querySelector('#viewing-lightbox');
  const links = Array.from(document.querySelectorAll('[data-gallery]'));
  const unique = [...new Map(links.map(link => [link.href, link])).values()];
  let selected = 0;
  const show = index => {
    selected = (index + unique.length) % unique.length;
    const link = unique[selected];
    dialog.querySelector('img').src = link.href;
    dialog.querySelector('img').alt = link.dataset.caption || 'Фото квартиры';
    dialog.querySelector('[data-lightbox-caption]').textContent = `${selected + 1} / ${unique.length}${link.dataset.caption ? ' · ' + link.dataset.caption : ''}`;
  };
  links.forEach(link => link.addEventListener('click', event => {
    if (!dialog.showModal) return;
    event.preventDefault(); show(unique.findIndex(item => item.href === link.href)); dialog.showModal();
  }));
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  dialog.querySelector('[data-prev]').addEventListener('click', () => show(selected - 1));
  dialog.querySelector('[data-next]').addEventListener('click', () => show(selected + 1));
  dialog.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft') {event.preventDefault(); show(selected - 1);}
    if (event.key === 'ArrowRight') {event.preventDefault(); show(selected + 1);}
  });
})();
