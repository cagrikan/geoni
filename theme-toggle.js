/* GEONI paylasilan tema toggle — rehber, guides ve lig sayfalarinda.
   Ana site ile ayni localStorage anahtari (geoni_site_theme) => tercih
   tum sitede paylasilir. Butonu kendisi enjekte eder; sayfaya sadece
   <head> icinde <script src="/theme-toggle.js"></script> eklemek yeter. */
(function () {
  var KEY = 'geoni_site_theme';
  var SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>';
  var MOON = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>';

  // Kayitli tercih yoksa isletim sistemini dinle. Sabit 'light' donuluyordu:
  // koyu temadaki kullanici rehber/guides sayfalarini bembeyaz aliyordu.
  function tercih() {
    var t = localStorage.getItem(KEY);
    if (t === 'light' || t === 'dark') return t;
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }

  function apply(t) {
    document.documentElement.setAttribute('data-theme', t === 'dark' ? 'dark' : 'light');
    var icon = document.getElementById('gn-theme-icon');
    if (icon) icon.innerHTML = (t === 'dark' ? SUN : MOON);
  }

  // Paint'ten once uygula (FOUC yok)
  apply(tercih());

  function mount() {
    if (document.getElementById('gn-theme-toggle')) return;
    var b = document.createElement('button');
    b.id = 'gn-theme-toggle';
    b.type = 'button';
    b.title = 'Tema';
    b.setAttribute('aria-label', 'Temayi degistir');
    b.innerHTML = '<svg id="gn-theme-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></svg>';
    b.addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      var next = cur === 'dark' ? 'light' : 'dark';
      localStorage.setItem(KEY, next);
      apply(next);
    });
    document.body.appendChild(b);
    apply(tercih()); // buton geldi, ikonu ayarla
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
