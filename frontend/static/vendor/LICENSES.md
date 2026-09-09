Vendored frontend libraries used by FirmaDoc.

- PDF.js 4.10.38 (`pdf.mjs`, `pdf.worker.mjs`), Apache License 2.0.
  Source distribution: https://www.npmjs.com/package/pdfjs-dist
- Signature Pad 4.2.0 (`signature_pad.umd.min.js`), MIT License.
  Source distribution: https://www.npmjs.com/package/signature_pad
- Bootstrap 5.3.3 (`bootstrap.min.css`, `bootstrap.bundle.min.js`), MIT License.
  Source distribution: https://www.npmjs.com/package/bootstrap

These files are served locally by FirmaDoc. Production pages must not load PDF.js
or Signature Pad from a CDN.
