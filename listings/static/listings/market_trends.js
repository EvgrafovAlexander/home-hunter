(() => {
  'use strict';
  document.querySelector('#id_district')?.setAttribute('list', 'market-districts');
  document.querySelector('#id_microdistrict')?.setAttribute('list', 'market-microdistricts');
  const payload = document.querySelector('#market-points');
  if (!payload) return;
  const points = JSON.parse(payload.textContent);
  const charts = {
    prices: [['median', 'Медиана', '#156c65'], ['q25', '25%', '#8aacc8'], ['q75', '75%', '#8b75b4']],
    active: [['active', 'Активных', '#156c65']],
    flows: [['new', 'Новых', '#156c65'], ['removed', 'Снятых', '#c37435']],
    reductions: [['reduced_pct', 'Снизили цену, %', '#c37435']],
    matched: [['matched_change', 'Изменение, %', '#156c65']],
  };
  const ns = 'http://www.w3.org/2000/svg';
  const node = (tag, attrs = {}, text = '') => {
    const el = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
    el.textContent = text;
    return el;
  };
  const format = value => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(value);
  document.querySelectorAll('[data-market-chart]').forEach(container => {
    const series = charts[container.dataset.marketChart];
    const values = points.flatMap(point => series.map(([key]) => point[key])).filter(value => value !== null);
    if (!values.length) {
      container.textContent = 'Недостаточно наблюдений. Причины указаны в таблице ниже.';
      return;
    }
    const svg = node('svg', {viewBox: '0 0 560 260', role: 'img', 'aria-label': container.closest('article').querySelector('h2').textContent});
    const min = Math.min(0, ...values), max = Math.max(...values, min + 1);
    const x = index => 75 + index * 460 / Math.max(1, points.length - 1);
    const y = value => 215 - (value - min) * 190 / (max - min);
    for (let tick = 0; tick <= 4; tick++) {
      const value = min + (max - min) * tick / 4;
      svg.append(node('line', {x1: 75, x2: 535, y1: y(value), y2: y(value), stroke: '#e2e8f0'}));
      svg.append(node('text', {x: 68, y: y(value) + 4, 'text-anchor': 'end', fill: '#526173', 'font-size': 11}, format(Math.round(value * 10) / 10)));
    }
    const labels = new Set([0, Math.floor((points.length - 1) / 2), points.length - 1]);
    labels.forEach(index => svg.append(node('text', {x: x(index), y: 244, 'text-anchor': 'middle', fill: '#526173', 'font-size': 11}, points[index].day.slice(5))));
    series.forEach(([key, label, color]) => {
      let previous = null;
      points.forEach((point, index) => {
        if (point[key] === null) { previous = null; return; }
        if (previous && previous.point.scope === point.scope && !point.scope_changed) {
          svg.append(node('line', {x1: x(previous.index), y1: y(previous.point[key]), x2: x(index), y2: y(point[key]), stroke: color, 'stroke-width': 2}));
        }
        const dot = node('circle', {cx: x(index), cy: y(point[key]), r: 4, fill: color, tabindex: 0});
        dot.append(node('title', {}, `${point.day}: ${label} — ${format(point[key])}; выборка цен: ${point.samples}; пар: ${point.matched_count}. ${point.reason}`));
        svg.append(dot);
        previous = {point, index};
      });
    });
    container.append(svg);
    const legend = document.createElement('div');
    legend.className = 'market-legend';
    series.forEach(([, label, color]) => {
      const item = document.createElement('span'); item.textContent = label; item.style.borderColor = color;
      legend.append(item);
    });
    container.append(legend);
  });
})();
