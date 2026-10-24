/* Shared controls for the platform and offline reports; never execute source text. */
(() => {
  'use strict';
  const preferences = {wrap:true, sync:true};
  function mount(root = document) {
    root.querySelectorAll('.evidence-view').forEach(view => {
      if (view.dataset.controlsReady) return;
      view.dataset.controlsReady = 'true';
      const wrap = view.querySelector('[data-evidence-wrap]');
      const sync = view.querySelector('[data-evidence-sync]');
      if (!wrap || !sync) return;
      wrap.checked = preferences.wrap; sync.checked = preferences.sync;
      view.classList.toggle('wrap-lines', wrap.checked);
      wrap.addEventListener('change', () => {
        preferences.wrap = wrap.checked;
        view.classList.toggle('wrap-lines', wrap.checked);
      });
      sync.addEventListener('change', () => { preferences.sync = sync.checked; });
      view.querySelectorAll('.fragment').forEach(fragment => {
        const panes = [...fragment.querySelectorAll('.code, .code-box')];
        if (panes.length !== 2) return;
        let synchronizing = false;
        panes.forEach((pane, index) => pane.addEventListener('scroll', () => {
          if (!sync.checked || synchronizing) return;
          synchronizing = true;
          const other = panes[1-index];
          const ratioX = pane.scrollLeft / Math.max(1, pane.scrollWidth - pane.clientWidth);
          const ratioY = pane.scrollTop / Math.max(1, pane.scrollHeight - pane.clientHeight);
          other.scrollLeft = ratioX * Math.max(0, other.scrollWidth - other.clientWidth);
          other.scrollTop = ratioY * Math.max(0, other.scrollHeight - other.clientHeight);
          requestAnimationFrame(() => { synchronizing = false; });
        }, {passive:true}));
      });
    });
  }
  window.CodeLensEvidence = {mount};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => mount());
  else mount();
})();
