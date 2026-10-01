export function normalizeRect(rect) {
  const left = Math.min(rect.x0, rect.x1);
  const top = Math.min(rect.y0, rect.y1);
  const right = Math.max(rect.x0, rect.x1);
  const bottom = Math.max(rect.y0, rect.y1);
  return {
    x: left,
    y: top,
    width: right - left,
    height: bottom - top,
  };
}

export function clampScreenRect(rect, viewport) {
  const normalized = normalizeRect(rect);
  const x = Math.max(0, Math.min(normalized.x, viewport.width));
  const y = Math.max(0, Math.min(normalized.y, viewport.height));
  const right = Math.max(0, Math.min(normalized.x + normalized.width, viewport.width));
  const bottom = Math.max(0, Math.min(normalized.y + normalized.height, viewport.height));
  return {
    x,
    y,
    width: Math.max(0, right - x),
    height: Math.max(0, bottom - y),
  };
}

export function screenRectToPdfRect(screenRect, viewport, pageInfo) {
  const rect = clampScreenRect(
    {
      x0: screenRect.x,
      y0: screenRect.y,
      x1: screenRect.x + screenRect.width,
      y1: screenRect.y + screenRect.height,
    },
    viewport,
  );
  if (rect.width <= 0 || rect.height <= 0) {
    throw new Error("El rectangulo debe tener ancho y alto positivos");
  }

  const topLeftPdf = viewport.convertToPdfPoint(rect.x, rect.y);
  const bottomRightPdf = viewport.convertToPdfPoint(rect.x + rect.width, rect.y + rect.height);
  const minX = Math.min(topLeftPdf[0], bottomRightPdf[0]);
  const maxX = Math.max(topLeftPdf[0], bottomRightPdf[0]);
  const minY = Math.min(topLeftPdf[1], bottomRightPdf[1]);
  const maxY = Math.max(topLeftPdf[1], bottomRightPdf[1]);

  const rotation = Number(pageInfo.rotation || 0);
  if (rotation !== 0) {
    return rotatedPdfBoxToVisibleRect({ minX, maxX, minY, maxY }, pageInfo);
  }

  return roundPdfRect({
    pagina: pageInfo.page,
    posx: minX,
    posy: Number(pageInfo.height) - maxY,
    ancho: maxX - minX,
    alto: maxY - minY,
    rotaci: rotation,
  });
}

export function pdfRectToScreenRect(pdfRect, viewport, pageInfo) {
  const rotation = Number(pageInfo.rotation || 0);
  if (rotation !== 0) {
    return visibleRectToRotatedScreenRect(pdfRect, viewport, pageInfo);
  }

  const x0 = Number(pdfRect.posx);
  const y0 = Number(pageInfo.height) - Number(pdfRect.posy);
  const x1 = x0 + Number(pdfRect.ancho);
  const y1 = y0 - Number(pdfRect.alto);
  const p0 = viewport.convertToViewportPoint(x0, y0);
  const p1 = viewport.convertToViewportPoint(x1, y1);
  const normalized = normalizeRect({ x0: p0[0], y0: p0[1], x1: p1[0], y1: p1[1] });
  return normalized;
}

function rotatedPdfBoxToVisibleRect(box, pageInfo) {
  const rotation = Number(pageInfo.rotation || 0);
  const pageWidth = Number(pageInfo.width);
  const pageHeight = Number(pageInfo.height);
  let rect;
  if (rotation === 90) {
    rect = {
      pagina: pageInfo.page,
      posx: pageWidth - box.maxY,
      posy: box.minX,
      ancho: box.maxY - box.minY,
      alto: box.maxX - box.minX,
      rotaci: rotation,
    };
  } else if (rotation === 180) {
    rect = {
      pagina: pageInfo.page,
      posx: pageWidth - box.maxX,
      posy: box.minY,
      ancho: box.maxX - box.minX,
      alto: box.maxY - box.minY,
      rotaci: rotation,
    };
  } else if (rotation === 270) {
    rect = {
      pagina: pageInfo.page,
      posx: box.minY,
      posy: pageHeight - box.maxX,
      ancho: box.maxY - box.minY,
      alto: box.maxX - box.minX,
      rotaci: rotation,
    };
  } else {
    throw new Error("Rotacion no soportada");
  }
  return roundPdfRect(rect);
}

function visibleRectToRotatedScreenRect(pdfRect, viewport, pageInfo) {
  const rotation = Number(pageInfo.rotation || 0);
  const x = Number(pdfRect.posx);
  const y = Number(pdfRect.posy);
  const width = Number(pdfRect.ancho);
  const height = Number(pdfRect.alto);
  const pageWidth = Number(pageInfo.width);
  const pageHeight = Number(pageInfo.height);
  let p0;
  let p1;
  if (rotation === 90) {
    p0 = [y, pageWidth - x];
    p1 = [y + height, pageWidth - x - width];
  } else if (rotation === 180) {
    p0 = [pageWidth - x, y];
    p1 = [pageWidth - x - width, y + height];
  } else if (rotation === 270) {
    p0 = [pageHeight - y, x];
    p1 = [pageHeight - y - height, x + width];
  } else {
    throw new Error("Rotacion no soportada");
  }
  const s0 = viewport.convertToViewportPoint(p0[0], p0[1]);
  const s1 = viewport.convertToViewportPoint(p1[0], p1[1]);
  return normalizeRect({ x0: s0[0], y0: s0[1], x1: s1[0], y1: s1[1] });
}

export function validatePdfRect(pdfRect, pageInfo) {
  const x = Number(pdfRect.posx);
  const y = Number(pdfRect.posy);
  const width = Number(pdfRect.ancho);
  const height = Number(pdfRect.alto);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height)) {
    return false;
  }
  if (x < 0 || y < 0 || width <= 0 || height <= 0) {
    return false;
  }
  return x + width <= Number(pageInfo.width) && y + height <= Number(pageInfo.height);
}

function roundPdfRect(rect) {
  return {
    pagina: Number(rect.pagina),
    posx: round4(rect.posx),
    posy: round4(rect.posy),
    ancho: round4(rect.ancho),
    alto: round4(rect.alto),
    rotaci: Number(rect.rotaci || 0),
  };
}

function round4(value) {
  return Number(Number(value).toFixed(4));
}

