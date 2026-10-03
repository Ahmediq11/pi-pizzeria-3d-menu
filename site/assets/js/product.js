// Product page behaviour: loading bar, hide the gesture hint after the first
// interaction, and fall back to the photo if the 3D model cannot be shown.
const viewer = document.getElementById('viewer');
const hint = document.getElementById('hint');

if (viewer) {
  const bar = viewer.querySelector('.progress');
  viewer.addEventListener('progress', (e) => {
    const p = e.detail.totalProgress;
    bar.firstElementChild.style.width = `${Math.round(p * 100)}%`;
    bar.classList.toggle('done', p >= 1);
  });

  const hideHint = () => hint && hint.classList.add('gone');
  viewer.addEventListener('camera-change', (e) => {
    if (e.detail.source === 'user-interaction') hideHint();
  });

  const fail = () => {
    const box = viewer.querySelector('.viewer-error');
    if (box) box.hidden = false;
    hideHint();
  };
  viewer.addEventListener('error', fail);
  // model-viewer is a custom element: if the module never registers (very old browser), show the photo.
  setTimeout(() => { if (!customElements.get('model-viewer')) fail(); }, 6000);
}
