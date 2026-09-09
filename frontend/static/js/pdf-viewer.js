import * as pdfjsLib from "../vendor/pdf.mjs";
import { labIdentityHeaders } from "./api.js";
import { pdfRectToScreenRect, screenRectToPdfRect, validatePdfRect } from "./pdf-coordinates.js";
import { hasDirtyState } from "./preparation-state.js";
import { RenderSequencer } from "./render-sequencer.js";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.mjs";

const MIN_RECT = 8;

export class PdfViewer {
  constructor(options) {
    this.container = options.container;
    this.thumbs = options.thumbs;
    this.status = options.status;
    this.userProvider = options.userProvider;
    this.onSelectionChange = options.onSelectionChange || (() => {});
    this.onDirtyChange = options.onDirtyChange || (() => {});
    this.onPositionsChange = options.onPositionsChange || (() => {});
    this.editable = Boolean(options.editable);
    this.pdfDoc = null;
    this.pages = [];
    this.pageInfo = [];
    this.positions = [];
    this.selectedId = null;
    this.zoom = 1;
    this.mode = "MANUSCRITA";
    this.signer = "";
    this.order = 1;
    this.dragState = null;
    this.dirty = false;
    this.renderSequencer = new RenderSequencer();
  }

  async load(url, pageInfo, positions = []) {
    this.pageInfo = pageInfo;
    this.positions = positions.map((position, index) => ({
      ...position,
      id: position.id || `pos-${index}-${Date.now()}`,
      saved: position.saved !== false,
      dirty: false,
    }));
    this.selectedId = null;
    this._setDirty(false);
    this.container.innerHTML = "";
    this.pages = [];
    this._setStatus("Cargando PDF");
    const task = pdfjsLib.getDocument({
      url,
      httpHeaders: labIdentityHeaders(this.userProvider()),
      withCredentials: false,
    });
    this.pdfDoc = await task.promise;
    await this.render();
    this._setStatus("");
  }

  async render() {
    return this.renderSequencer.schedule((token) => this._renderInternal(token));
  }

  async _renderInternal(token) {
    const fragment = document.createDocumentFragment();
    const nextPages = [];
    for (let pageNumber = 1; pageNumber <= this.pdfDoc.numPages; pageNumber += 1) {
      const page = await this.pdfDoc.getPage(pageNumber);
      const viewport = page.getViewport({ scale: this.zoom });
      const wrapper = document.createElement("section");
      wrapper.className = "pdf-page";
      wrapper.dataset.page = String(pageNumber);
      wrapper.style.width = `${viewport.width}px`;
      wrapper.style.height = `${viewport.height}px`;

      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      context.setTransform(outputScale, 0, 0, outputScale, 0, 0);

      const overlay = document.createElement("div");
      overlay.className = "pdf-overlay";
      overlay.setAttribute("aria-label", `Pagina ${pageNumber}`);
      overlay.tabIndex = 0;

      wrapper.append(canvas, overlay);
      await page.render({ canvasContext: context, viewport }).promise;
      if (!token.isCurrent()) return;

      const pageState = { pageNumber, page, viewport, wrapper, overlay };
      nextPages.push(pageState);
      fragment.appendChild(wrapper);
      this._bindOverlay(pageState);
      this._renderPositionsForPage(pageState);
    }
    this.pages = nextPages;
    this.container.replaceChildren(fragment);
    this._renderThumbs();
  }

  setMode(mode) {
    this.mode = mode;
  }

  setSigner(usrid) {
    this.signer = usrid;
  }

  setOrder(order) {
    this.order = Number(order || 1);
  }

  async setZoom(zoom) {
    this.zoom = Number(zoom);
    if (this.pdfDoc) {
      await this.render();
    }
  }

  getPositions() {
    return this.positions.map((position) => ({
      pagina: position.pagina,
      posx: position.posx,
      posy: position.posy,
      ancho: position.ancho,
      alto: position.alto,
      rotaci: position.rotaci || 0,
      orden: position.orden,
      tipfir: position.tipfir,
      usrid: position.usrid,
    }));
  }

  isDirty() {
    return hasDirtyState({ dirty: this.dirty, positions: this.positions });
  }

  markClean() {
    this.positions = this.positions.map((position) => ({ ...position, saved: true, dirty: false }));
    this._setDirty(false);
    this.onPositionsChange(this.getPositions());
  }

  updateSelectedProperties(properties) {
    const selected = this.positions.find((position) => position.id === this.selectedId);
    if (!selected) return;
    const next = {};
    if (properties.tipfir) next.tipfir = properties.tipfir;
    if (properties.usrid !== undefined) next.usrid = String(properties.usrid).trim().toLowerCase();
    if (properties.orden !== undefined) next.orden = Number(properties.orden || 1);
    Object.assign(selected, next, { saved: false, dirty: true });
    this._setDirty(true);
    this.onSelectionChange(selected);
    this.onPositionsChange(this.getPositions());
    void this.render();
  }

  deleteSelected() {
    if (!this.selectedId) return;
    this.positions = this.positions.filter((position) => position.id !== this.selectedId);
    this.selectedId = null;
    this._setDirty(true);
    this.onPositionsChange(this.getPositions());
    void this.render();
    this.onSelectionChange(null);
  }

  select(id) {
    this.selectedId = id;
    this._refreshAllPositionElements();
    const selected = this.positions.find((position) => position.id === id) || null;
    this.onSelectionChange(selected);
  }

  scrollToPage(pageNumber) {
    const page = this.pages.find((entry) => entry.pageNumber === pageNumber);
    page?.wrapper.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  _bindOverlay(pageState) {
    if (!this.editable) return;
    pageState.overlay.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      if (event.target !== pageState.overlay) return;
      const start = this._eventPoint(event, pageState.overlay);
      this.dragState = {
        type: "create",
        pageState,
        start,
        current: start,
        element: this._createGhost(pageState.overlay, start.x, start.y),
      };
      pageState.overlay.setPointerCapture(event.pointerId);
    });

    pageState.overlay.addEventListener("pointermove", (event) => this._handlePointerMove(event));
    pageState.overlay.addEventListener("pointerup", (event) => this._handlePointerUp(event));
    pageState.overlay.addEventListener("keydown", (event) => {
      if (event.key === "Delete" || event.key === "Backspace") {
        this.deleteSelected();
      }
    });
  }

  _renderPositionsForPage(pageState) {
    for (const position of this.positions.filter((item) => item.pagina === pageState.pageNumber)) {
      this._renderPosition(pageState, position);
    }
  }

  _renderPosition(pageState, position) {
    const pageInfo = this.pageInfo[pageState.pageNumber - 1];
    if (!pageInfo || !validatePdfRect(position, pageInfo)) return;
    const rect = pdfRectToScreenRect(position, pageState.viewport, pageInfo);
    const element = document.createElement("button");
    element.type = "button";
    element.className = `signature-box ${position.tipfir === "INTERNA" ? "internal" : "handwritten"}`;
    if (position.saved) element.classList.add("saved");
    if (position.id === this.selectedId) element.classList.add("selected");
    element.dataset.id = position.id;
    element.style.left = `${rect.x}px`;
    element.style.top = `${rect.y}px`;
    element.style.width = `${rect.width}px`;
    element.style.height = `${rect.height}px`;
    element.innerHTML = `<span>${position.tipfir === "INTERNA" ? "Interna" : "Manuscrita"}</span><i></i>`;
    element.addEventListener("click", (event) => {
      event.stopPropagation();
      this.select(position.id);
    });
    if (this.editable) {
      element.addEventListener("pointerdown", (event) => this._startMove(event, pageState, position, rect));
      element.querySelector("i").addEventListener("pointerdown", (event) => this._startResize(event, pageState, position, rect));
    }
    pageState.overlay.appendChild(element);
  }

  _startMove(event, pageState, position, rect) {
    if (event.button !== 0 || event.target.tagName === "I") return;
    event.preventDefault();
    event.stopPropagation();
    this.select(position.id);
    this.dragState = {
      type: "move",
      pageState,
      position,
      origin: rect,
      start: this._eventPoint(event, pageState.overlay),
    };
    pageState.overlay.setPointerCapture(event.pointerId);
  }

  _startResize(event, pageState, position, rect) {
    event.preventDefault();
    event.stopPropagation();
    this.select(position.id);
    this.dragState = {
      type: "resize",
      pageState,
      position,
      origin: rect,
      start: this._eventPoint(event, pageState.overlay),
    };
    pageState.overlay.setPointerCapture(event.pointerId);
  }

  _handlePointerMove(event) {
    if (!this.dragState) return;
    const state = this.dragState;
    const point = this._eventPoint(event, state.pageState.overlay);
    if (state.type === "create") {
      state.current = point;
      const rect = this._screenFromPoints(state.start, state.current);
      Object.assign(state.element.style, {
        left: `${rect.x}px`,
        top: `${rect.y}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
      });
      return;
    }

    const deltaX = point.x - state.start.x;
    const deltaY = point.y - state.start.y;
    let rect;
    if (state.type === "move") {
      rect = {
        x: state.origin.x + deltaX,
        y: state.origin.y + deltaY,
        width: state.origin.width,
        height: state.origin.height,
      };
    } else {
      rect = {
        x: state.origin.x,
        y: state.origin.y,
        width: Math.max(MIN_RECT, state.origin.width + deltaX),
        height: Math.max(MIN_RECT, state.origin.height + deltaY),
      };
    }
    rect = this._clampRect(rect, state.pageState.viewport);
    const element = state.pageState.overlay.querySelector(`[data-id="${state.position.id}"]`);
    if (element) {
      element.style.left = `${rect.x}px`;
      element.style.top = `${rect.y}px`;
      element.style.width = `${rect.width}px`;
      element.style.height = `${rect.height}px`;
    }
  }

  _handlePointerUp(event) {
    if (!this.dragState) return;
    const state = this.dragState;
    const pageInfo = this.pageInfo[state.pageState.pageNumber - 1];
    if (state.type === "create") {
      const rect = this._screenFromPoints(state.start, this._eventPoint(event, state.pageState.overlay));
      state.element.remove();
      if (rect.width >= MIN_RECT && rect.height >= MIN_RECT && this.signer) {
        const pdfRect = screenRectToPdfRect(rect, state.pageState.viewport, pageInfo);
        const position = {
          id: `pos-${crypto.randomUUID ? crypto.randomUUID() : Date.now()}`,
          ...pdfRect,
          tipfir: this.mode,
          usrid: this.signer,
          orden: this.order,
          saved: false,
          dirty: true,
        };
        this.positions.push(position);
        this._setDirty(true);
        this.onPositionsChange(this.getPositions());
        this.select(position.id);
      }
    } else {
      const element = state.pageState.overlay.querySelector(`[data-id="${state.position.id}"]`);
      if (element) {
        const rect = {
          x: Number.parseFloat(element.style.left),
          y: Number.parseFloat(element.style.top),
          width: Number.parseFloat(element.style.width),
          height: Number.parseFloat(element.style.height),
        };
        const pdfRect = screenRectToPdfRect(rect, state.pageState.viewport, pageInfo);
        Object.assign(state.position, pdfRect, { saved: false, dirty: true });
        this._setDirty(true);
        this.onPositionsChange(this.getPositions());
        this.onSelectionChange(state.position);
      }
    }
    this.dragState = null;
    void this.render();
  }

  _refreshAllPositionElements() {
    for (const element of this.container.querySelectorAll(".signature-box")) {
      element.classList.toggle("selected", element.dataset.id === this.selectedId);
    }
  }

  _renderThumbs() {
    if (!this.thumbs) return;
    this.thumbs.innerHTML = "";
    for (const page of this.pages) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "thumb-button";
      button.textContent = `Pagina ${page.pageNumber}`;
      button.addEventListener("click", () => this.scrollToPage(page.pageNumber));
      this.thumbs.appendChild(button);
    }
  }

  _createGhost(overlay, x, y) {
    const element = document.createElement("div");
    element.className = "signature-box ghost";
    element.style.left = `${x}px`;
    element.style.top = `${y}px`;
    element.style.width = "0";
    element.style.height = "0";
    overlay.appendChild(element);
    return element;
  }

  _eventPoint(event, target) {
    const rect = target.getBoundingClientRect();
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
  }

  _screenFromPoints(start, current) {
    return this._clampRect(
      {
        x: Math.min(start.x, current.x),
        y: Math.min(start.y, current.y),
        width: Math.abs(current.x - start.x),
        height: Math.abs(current.y - start.y),
      },
      this.dragState.pageState.viewport,
    );
  }

  _clampRect(rect, viewport) {
    const x = Math.max(0, Math.min(rect.x, viewport.width));
    const y = Math.max(0, Math.min(rect.y, viewport.height));
    return {
      x,
      y,
      width: Math.max(0, Math.min(rect.width, viewport.width - x)),
      height: Math.max(0, Math.min(rect.height, viewport.height - y)),
    };
  }

  _setStatus(message) {
    if (this.status) {
      this.status.textContent = message;
    }
  }

  _setDirty(value) {
    const next = Boolean(value);
    if (this.dirty === next) return;
    this.dirty = next;
    this.onDirtyChange(next);
  }
}
