const DATA_URL_PREFIX = "data:image/png;base64,";

export function pngDataUrlBinarySize(dataUrl) {
  if (!dataUrl || !dataUrl.startsWith(DATA_URL_PREFIX)) {
    return 0;
  }
  const base64 = dataUrl.slice(DATA_URL_PREFIX.length).replace(/\s/g, "");
  const padding = base64.endsWith("==") ? 2 : base64.endsWith("=") ? 1 : 0;
  return Math.floor((base64.length * 3) / 4) - padding;
}

export function isPngDataUrlWithinLimit(dataUrl, maxBytes) {
  return pngDataUrlBinarySize(dataUrl) <= Number(maxBytes || 0);
}

