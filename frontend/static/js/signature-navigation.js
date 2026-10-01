export function buildSignatureRedirectUrl(firid, { autoQr = false, returnUrl = "" } = {}) {
  const query = new URLSearchParams();
  if (autoQr) query.set("autoQr", "true");
  if (returnUrl) query.set("returnUrl", returnUrl);

  const queryString = query.toString();
  return `/firmas/${encodeURIComponent(firid)}${queryString ? `?${queryString}` : ""}`;
}
