// Picks rupees or US dollars before the page paints, so prices do not flash: the visitor's own choice if they made
// one, else rupees in India (by time zone or an Indian locale) and dollars elsewhere. The HTML always carries both
// amounts and CSS shows one (site.css); without this script the page stays in rupees. Charges are always in rupees.
(function () {
  var choice = null;
  try {
    choice = localStorage.getItem("site.currency");
  } catch (e) {}
  if (choice !== "inr" && choice !== "usd") {
    var zone = "";
    try {
      zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (e) {}
    var langs = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""];
    var india = zone === "Asia/Kolkata" || zone === "Asia/Calcutta";
    for (var i = 0; i < langs.length; i++) if (/-IN$/i.test(langs[i])) india = true;
    choice = india ? "inr" : "usd";
  }
  document.documentElement.setAttribute("data-currency", choice);
})();
