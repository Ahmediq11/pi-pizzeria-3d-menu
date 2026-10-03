// HTML templates for the static site. One template per page type; build.mjs
// renders the product template once per entry in products/products.json.

export const esc = (s = '') => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const fmt = (n) => n.toLocaleString('en-US');

function head({ cfg, url, title, description, image, extra = '' }) {
  const a = (p) => cfg.basePath + p;
  return `<!doctype html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}">
<meta name="theme-color" content="#faf8f5">
<link rel="canonical" href="${esc(url)}">
<meta property="og:type" content="website">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
${image ? `<meta property="og:image" content="${esc(cfg.baseUrl + image)}">` : ''}
<link rel="icon" type="image/png" sizes="64x64" href="${a('/assets/brand/pi-64.png')}">
<link rel="apple-touch-icon" href="${a('/assets/brand/pi-192.png')}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Baloo+Bhaijaan+2:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="${a('/assets/css/site.css')}">
${extra}
</head>`;
}

function brandBar(cfg, { back = false } = {}) {
  const a = (p) => cfg.basePath + p;
  return `<header class="bar">
  <a class="brand" href="${a('/')}" aria-label="${esc(cfg.restaurant.name.en)} – menu">
    <img src="${a('/assets/brand/pi-192.png')}" alt="" width="36" height="36">
    <span class="brand-name">${esc(cfg.restaurant.name.en)}<span lang="ar" dir="rtl">${esc(cfg.restaurant.name.ar)}</span></span>
  </a>
  ${back ? `<a class="bar-link" href="${a('/')}">Menu <span lang="ar" dir="rtl">القائمة</span></a>` : ''}
</header>`;
}

function footer(cfg) {
  return `<footer class="foot">
  <span>${esc(cfg.restaurant.group)}</span>
  <a href="${esc(cfg.restaurant.website)}" rel="noopener">${esc(cfg.restaurant.website.replace(/^https?:\/\//, ''))}</a>
</footer>`;
}

function priceList(p, cfg) {
  if (!p.prices.length) return '';
  const cur = cfg.currency.label;
  const rows = p.prices.map((pr) => {
    const size = cfg.sizes[pr.size] || { en: pr.size, ar: '' };
    return `<li>
      <span class="size">${size.en ? esc(size.en) : '&nbsp;'}${size.ar ? ` <span lang="ar" dir="rtl">${esc(size.ar)}</span>` : ''}</span>
      <span class="dots" aria-hidden="true"></span>
      <span class="amount">${fmt(pr.amount)} <small>${esc(cur.en)}</small></span>
    </li>`;
  }).join('');
  return `<ul class="prices">${rows}</ul>`;
}

export function productPage({ p, cfg, url, category, index, total }) {
  const a = (x) => cfg.basePath + x;
  const view = { ...cfg.viewer.default, ...(cfg.viewer[p.category] || {}), ...(p.viewer || {}) };
  const desc = [p.description.en, p.description.ar].filter(Boolean).join(' · ');
  const title = `${p.name.en} · ${p.name.ar} — ${cfg.restaurant.name.en}`;
  return `${head({
    cfg, url, title, image: p.image,
    description: `${p.name.en} (${category.name.en}) in 3D${desc ? ` — ${desc}` : ''}. ${cfg.restaurant.name.en}`,
    extra: `<script>self.ModelViewerElement = { meshoptDecoderLocation: '${a('/vendor/meshopt_decoder.js')}' };</script>
<script type="module" src="${a('/vendor/model-viewer.min.js')}"></script>
<script type="module" src="${a('/assets/js/product.js')}"></script>`,
  })}
<body class="product-page" data-slug="${esc(p.slug)}">
${brandBar(cfg, { back: true })}
<main>
  <section class="title">
    <p class="eyebrow">
      <span>${esc(category.name.en)}${p.menuNumber ? ` · No. ${p.menuNumber}` : ''}</span>
      <span lang="ar" dir="rtl">${esc(category.name.ar)}</span>
    </p>
    <h1>${esc(p.name.en)}</h1>
    <p class="name-ar" lang="ar" dir="rtl">${esc(p.name.ar)}</p>
  </section>

  <section class="stage" aria-label="3D model">
    <model-viewer id="viewer"
      src="${a(p.model)}"
      poster="${a(p.poster)}"
      alt="3D model of ${esc(p.name.en)}"
      camera-controls touch-action="pan-y"
      camera-orbit="${esc(view.cameraOrbit)}" camera-target="${esc(view.cameraTarget)}"
      min-camera-orbit="${esc(view.minCameraOrbit)}" max-camera-orbit="${esc(view.maxCameraOrbit)}"
      interpolation-decay="120"
      auto-rotate auto-rotate-delay="1800" rotation-per-second="14deg"
      interaction-prompt="none"
      shadow-intensity="1" shadow-softness="0.9"
      environment-image="neutral" tone-mapping="neutral" exposure="1.0"
      ar ar-modes="webxr scene-viewer quick-look" ar-scale="auto" ar-placement="floor"
      loading="eager" reveal="auto">
      <div class="progress" slot="progress-bar"><span></span></div>
      <button class="ar-btn" slot="ar-button" type="button">View on your table <span lang="ar" dir="rtl">على طاولتك</span></button>
      <div class="viewer-error" hidden>
        <img src="${a(p.image)}" alt="${esc(p.name.en)}">
        <p>3D view isn’t available on this device. <span lang="ar" dir="rtl">العرض ثلاثي الأبعاد غير متاح على هذا الجهاز.</span></p>
      </div>
    </model-viewer>
    <p class="hint" id="hint">
      <span class="hint-icon" aria-hidden="true"></span>
      <span>Drag to rotate · pinch to zoom</span>
      <span lang="ar" dir="rtl">اسحب للتدوير · قرّب بإصبعين</span>
    </p>
  </section>

  <section class="info">
    ${desc ? `<p class="desc"><span>${esc(p.description.en)}</span>${p.description.ar ? ` <span lang="ar" dir="rtl">${esc(p.description.ar)}</span>` : ''}</p>` : ''}
    ${priceList(p, cfg)}
    ${p.placeholders.includes('prices') ? `<p class="ask">Price on request <span lang="ar" dir="rtl">السعر عند الطلب</span></p>` : ''}
  </section>

  <section class="photo">
    <figure>
      <img src="${a(p.image)}" alt="${esc(p.name.en)} as photographed for the menu" width="800" height="800" loading="lazy">
      <figcaption>From our menu <span lang="ar" dir="rtl">من قائمتنا</span></figcaption>
    </figure>
    <p class="note">The 3D model is an illustration built from the menu photo; the served dish may look slightly different.
      <span lang="ar" dir="rtl">النموذج ثلاثي الأبعاد توضيحي ومبني على صورة القائمة، وقد يختلف الطبق المقدَّم قليلاً.</span></p>
  </section>

  <nav class="pager" aria-label="More dishes">
    ${index.prev ? `<a href="${a(index.prev.path)}"><small>Previous</small>${esc(index.prev.name)}</a>` : '<span></span>'}
    <span class="count">${index.pos} / ${total}</span>
    ${index.next ? `<a href="${a(index.next.path)}" class="next"><small>Next</small>${esc(index.next.name)}</a>` : '<span></span>'}
  </nav>
</main>
${footer(cfg)}
</body>
</html>
`;
}

export function menuPage({ cfg, groups, url, menuPages }) {
  const a = (x) => cfg.basePath + x;
  const sections = groups.map(({ category, items }) => `
  <section class="cat" id="${esc(category.id)}">
    <h2><span>${esc(category.name.en)}</span><span lang="ar" dir="rtl">${esc(category.name.ar)}</span></h2>
    <ul class="cards">
      ${items.map(({ p, path }) => {
        const from = p.prices.length ? Math.min(...p.prices.map((x) => x.amount)) : null;
        return `<li><a class="card" href="${a(path)}">
          <img src="${a(p.image)}" alt="" width="800" height="800" loading="lazy">
          <span class="card-body">
            <span class="card-name">${esc(p.name.en)}</span>
            <span class="card-ar" lang="ar" dir="rtl">${esc(p.name.ar)}</span>
            <span class="card-price">${from !== null ? `${p.prices.length > 1 ? 'from ' : ''}${fmt(from)} ${esc(cfg.currency.label.en)}` : ''}</span>
          </span>
          <span class="badge3d" aria-label="3D">3D</span>
        </a></li>`;
      }).join('')}
    </ul>
  </section>`).join('');
  return `${head({ cfg, url, title: `${cfg.restaurant.name.en} · ${cfg.restaurant.name.ar} — Menu in 3D`, description: `${cfg.restaurant.name.en} menu: every dish in 3D.`, image: '/assets/brand/pi-512.png' })}
<body class="menu-page">
${brandBar(cfg)}
<main>
  <section class="hero">
    <img src="${a('/assets/brand/pi-512.png')}" alt="" width="120" height="120">
    <h1>${esc(cfg.restaurant.name.en)} <span lang="ar" dir="rtl">${esc(cfg.restaurant.name.ar)}</span></h1>
    <p>Tap any dish to see it in 3D <span lang="ar" dir="rtl">اضغط على أي طبق لمشاهدته ثلاثي الأبعاد</span></p>
    <nav class="jump">${groups.map(({ category }) => `<a href="#${esc(category.id)}">${esc(category.name.en)}</a>`).join('')}</nav>
  </section>
  ${sections}
  <section class="downloads">
    <a href="${a('/menu/' + menuPages.pdf)}" download>Download the menu (PDF) <span lang="ar" dir="rtl">تحميل القائمة</span></a>
  </section>
</main>
${footer(cfg)}
</body>
</html>
`;
}

export function qrSheetPage({ cfg, items, url }) {
  const a = (x) => cfg.basePath + x;
  return `${head({ cfg, url, title: `QR codes — ${cfg.restaurant.name.en}`, description: 'Printable QR codes, one per product.', extra: '<meta name="robots" content="noindex">' })}
<body class="qr-page">
${brandBar(cfg, { back: true })}
<main>
  <h1>Product QR codes</h1>
  <p class="muted">Each code opens its own product page. Base URL: <code>${esc(cfg.baseUrl)}</code></p>
  <ul class="qr-grid">
    ${items.map(({ p, link }) => `<li>
      <img src="${a(p.qr.replace(/\.png$/, '.svg'))}" alt="QR code for ${esc(p.name.en)}" width="180" height="180">
      <strong>${esc(p.name.en)}</strong><span lang="ar" dir="rtl">${esc(p.name.ar)}</span>
      <a href="${esc(link)}">${esc(link.replace(/^https?:\/\//, ''))}</a>
    </li>`).join('')}
  </ul>
</main>
</body>
</html>
`;
}

export function notFoundPage({ cfg }) {
  const a = (x) => cfg.basePath + x;
  return `${head({ cfg, url: cfg.baseUrl + '/404.html', title: `Not found — ${cfg.restaurant.name.en}`, description: 'Page not found' })}
<body class="menu-page">
${brandBar(cfg)}
<main class="nf">
  <h1>This dish isn’t on the menu</h1>
  <p lang="ar" dir="rtl">هذا الطبق غير موجود في القائمة</p>
  <a class="btn" href="${a('/')}">See the menu <span lang="ar" dir="rtl">عرض القائمة</span></a>
</main>
</body>
</html>
`;
}
