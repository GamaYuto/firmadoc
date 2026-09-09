import assert from "node:assert/strict";
import { screenRectToPdfRect, pdfRectToScreenRect, validatePdfRect } from "../static/js/pdf-coordinates.js";

function viewport(scale, pageHeight = 842) {
  return {
    width: 595 * scale,
    height: pageHeight * scale,
    convertToPdfPoint(x, y) {
      return [x / scale, pageHeight - y / scale];
    },
    convertToViewportPoint(x, y) {
      return [x * scale, (pageHeight - y) * scale];
    },
  };
}

const page1 = { page: 1, width: 595, height: 842, rotation: 0 };
const baseScreen = { x: 72, y: 96, width: 180, height: 70 };

for (const zoom of [0.75, 1, 1.5]) {
  const scaled = {
    x: baseScreen.x * zoom,
    y: baseScreen.y * zoom,
    width: baseScreen.width * zoom,
    height: baseScreen.height * zoom,
  };
  const pdf = screenRectToPdfRect(scaled, viewport(zoom), page1);
  assert.deepEqual(pdf, {
    pagina: 1,
    posx: 72,
    posy: 96,
    ancho: 180,
    alto: 70,
    rotaci: 0,
  });
  assert.equal(validatePdfRect(pdf, page1), true);
}

const page2 = { page: 2, width: 595, height: 842, rotation: 0 };
const pdfPage2 = screenRectToPdfRect(baseScreen, viewport(1), page2);
assert.equal(pdfPage2.pagina, 2);

const resizedViewport = viewport(1.25);
const roundTripScreen = pdfRectToScreenRect(
  { pagina: 1, posx: 72, posy: 96, ancho: 180, alto: 70, rotaci: 0 },
  resizedViewport,
  page1,
);
assert.deepEqual(roundTripScreen, { x: 90, y: 120, width: 225, height: 87.5 });

assert.equal(
  validatePdfRect({ pagina: 1, posx: 590, posy: 830, ancho: 10, alto: 20, rotaci: 0 }, page1),
  false,
);

const clamped = screenRectToPdfRect({ x: -10, y: -10, width: 20, height: 20 }, viewport(1), page1);
assert.deepEqual(clamped, { pagina: 1, posx: 0, posy: 0, ancho: 10, alto: 10, rotaci: 0 });
