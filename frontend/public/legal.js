// Shared bilingual (EN default + RU) toggle for the legal pages. The page
// supplies its RU strings via `window.LEGAL_I18N_RU`; the EN copy is read from
// the rendered DOM (each translatable node carries a `data-i18n` key).
(function () {
  var I18N = { en: {}, ru: window.LEGAL_I18N_RU || {} };
  document.querySelectorAll("[data-i18n]").forEach(function (el) {
    I18N.en[el.getAttribute("data-i18n")] = el.innerHTML;
  });
  function applyLang(lang) {
    if (!I18N[lang]) lang = "en";
    document.documentElement.setAttribute("lang", lang);
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var v = I18N[lang][el.getAttribute("data-i18n")]; if (v != null) el.innerHTML = v;
    });
    document.querySelectorAll(".lang-btn").forEach(function (b) {
      b.setAttribute("data-active", b.getAttribute("data-lang-set") === lang ? "true" : "false");
    });
    try { localStorage.setItem("easycad_lang", lang); } catch (e) {}
  }
  document.querySelectorAll(".lang-btn").forEach(function (b) {
    b.addEventListener("click", function () { applyLang(b.getAttribute("data-lang-set")); });
  });
  var stored = "en";
  try { var s = localStorage.getItem("easycad_lang"); if (s === "ru" || s === "en") stored = s; } catch (e) {}
  applyLang(stored);
})();
