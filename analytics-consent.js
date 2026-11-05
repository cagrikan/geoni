/* global document, window */

(() => {
  const MEASUREMENT_ID = 'G-2MXW6CXM1N';
  const CONSENT_KEY = 'geoni_web_analytics_consent_v1';
  const COOKIE_DOMAIN = '.geoni.ai';
  const PUBLIC_PATHS = new Set(['/', '/en', '/privacy']);

  const normalisePath = (pathname) => {
    let path = pathname.replace(/\/index\.html$/, '') || '/';
    path = path.replace(/\.html$/, '');
    if (path.length > 1) path = path.replace(/\/$/, '');
    return path || '/';
  };

  const publicPath = normalisePath(window.location.pathname);
  if (
    !PUBLIC_PATHS.has(publicPath) ||
    window.location.search !== '' ||
    window.location.hash !== ''
  ) {
    return;
  }

  const english = document.documentElement.lang.toLowerCase().startsWith('en');
  const copy = english
    ? {
        title: 'Website analytics choice',
        body: 'With your permission, Google Analytics measures visits to GEONI public information pages only. Account, scan and application data are never sent to Analytics.',
        accept: 'Accept analytics',
        reject: 'Reject',
        manage: 'Analytics preferences',
        privacy: 'Privacy policy',
      }
    : {
        title: 'Web sitesi analiz tercihi',
        body: 'İzninizle Google Analytics yalnız GEONI’nin herkese açık bilgi sayfalarının ziyaretini ölçer. Hesap, tarama ve uygulama verileri Analytics’e gönderilmez.',
        accept: 'Analizi kabul et',
        reject: 'Reddet',
        manage: 'Analiz tercihleri',
        privacy: 'Gizlilik politikası',
      };

  window.dataLayer = window.dataLayer || [];
  const gtag =
    typeof window.gtag === 'function'
      ? window.gtag
      : function gtag() {
          window.dataLayer.push(arguments);
        };
  window.gtag = gtag;

  const deniedConsent = {
    ad_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
    analytics_storage: 'denied',
  };

  gtag('consent', 'default', {
    ...deniedConsent,
    functionality_storage: 'granted',
    security_storage: 'granted',
    wait_for_update: 500,
  });
  gtag('set', 'ads_data_redaction', true);
  gtag('set', 'allow_google_signals', false);

  let analyticsLoaded = false;

  const readConsent = () => {
    try {
      const value = window.localStorage.getItem(CONSENT_KEY);
      return value === 'granted' || value === 'denied' ? value : null;
    } catch {
      return null;
    }
  };

  const saveConsent = (value) => {
    try {
      window.localStorage.setItem(CONSENT_KEY, value);
    } catch {
      // The current-page choice still applies when browser storage is unavailable.
    }
  };

  const clearAnalyticsCookies = () => {
    const host = window.location.hostname;
    document.cookie.split(';').forEach((item) => {
      const name = item.split('=')[0].trim();
      if (!name.startsWith('_ga')) return;
      document.cookie = `${name}=; Max-Age=0; path=/; SameSite=Lax`;
      document.cookie = `${name}=; Max-Age=0; path=/; domain=${host}; SameSite=Lax`;
      document.cookie = `${name}=; Max-Age=0; path=/; domain=${COOKIE_DOMAIN}; SameSite=Lax`;
    });
  };

  const loadAnalytics = () => {
    gtag('consent', 'update', { analytics_storage: 'granted' });
    if (analyticsLoaded || document.getElementById('geoni-google-analytics')) return;
    analyticsLoaded = true;

    const pageLocation = `${window.location.origin}${window.location.pathname}`;
    gtag('js', new Date());
    gtag('config', MEASUREMENT_ID, {
      send_page_view: false,
      allow_google_signals: false,
      allow_ad_personalization_signals: false,
      cookie_flags: 'SameSite=Lax;Secure',
    });
    gtag('event', 'page_view', {
      page_location: pageLocation,
      page_path: publicPath,
      page_title: document.title,
      page_referrer: '',
    });

    const script = document.createElement('script');
    script.id = 'geoni-google-analytics';
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(MEASUREMENT_ID)}`;
    document.head.append(script);
  };

  const banner = document.createElement('section');
  banner.className = 'geoni-consent';
  banner.hidden = true;
  banner.tabIndex = -1;
  banner.setAttribute('role', 'dialog');
  banner.setAttribute('aria-labelledby', 'geoni-consent-title');
  banner.setAttribute('aria-describedby', 'geoni-consent-description');

  const text = document.createElement('div');
  text.className = 'geoni-consent__text';
  const title = document.createElement('strong');
  title.id = 'geoni-consent-title';
  title.textContent = copy.title;
  const description = document.createElement('p');
  description.id = 'geoni-consent-description';
  description.textContent = copy.body;
  const privacy = document.createElement('a');
  privacy.href = '/privacy';
  privacy.textContent = copy.privacy;
  text.append(title, description, privacy);

  const actions = document.createElement('div');
  actions.className = 'geoni-consent__actions';
  const reject = document.createElement('button');
  reject.type = 'button';
  reject.className = 'geoni-consent__button';
  reject.textContent = copy.reject;
  const accept = document.createElement('button');
  accept.type = 'button';
  accept.className = 'geoni-consent__button';
  accept.textContent = copy.accept;
  actions.append(reject, accept);
  banner.append(text, actions);

  const manage = document.createElement('button');
  manage.type = 'button';
  manage.className = 'geoni-consent-manage';
  manage.textContent = copy.manage;
  manage.hidden = true;

  const showBanner = () => {
    banner.hidden = false;
    manage.hidden = true;
    banner.focus();
  };

  const hideBanner = () => {
    banner.hidden = true;
    manage.hidden = false;
  };

  const setConsent = (value) => {
    saveConsent(value);
    if (value === 'granted') {
      loadAnalytics();
    } else {
      gtag('consent', 'update', deniedConsent);
      clearAnalyticsCookies();
    }
    hideBanner();
  };

  reject.addEventListener('click', () => setConsent('denied'));
  accept.addEventListener('click', () => setConsent('granted'));
  manage.addEventListener('click', showBanner);
  banner.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && readConsent()) hideBanner();
  });

  document.body.append(banner, manage);

  const storedConsent = readConsent();
  if (storedConsent === 'granted') {
    loadAnalytics();
    manage.hidden = false;
  } else if (storedConsent === 'denied') {
    manage.hidden = false;
  } else {
    showBanner();
  }
})();
