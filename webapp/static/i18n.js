/* Minimal browser i18n boundary. New UI code must use window.t("catalogue.key"). */
(() => {
  let messages = {};

  async function loadLocale(locale = "zh-CN") {
    const response = await fetch(`/static/i18n/${locale}.json`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Unable to load locale: ${locale}`);
    messages = await response.json();
  }

  window.t = (key, fallback = key) => messages[key] ?? fallback;
  window.loadLocale = loadLocale;
  window.loadLocale("zh-CN").catch((error) => console.error(error));
})();
