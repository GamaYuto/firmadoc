/**
 * FIRMADOC - Motor de lectura en voz alta para documentos PDF
 *
 * Utiliza PDF.js para extracción secuencial de texto y Web Speech API
 * (speechSynthesis) exclusivamente con voces locales del dispositivo.
 * Por privacidad y seguridad clínica:
 *  - No envía datos a servicios externos.
 *  - No almacena texto ni audio.
 *  - Rechaza automáticamente voces remotas.
 */

export const ReadAloudState = Object.freeze({
  IDLE: "IDLE",
  READY: "READY",
  PREPARING: "PREPARING",
  READING: "READING",
  PAUSED: "PAUSED",
  COMPLETED: "COMPLETED",
  UNAVAILABLE: "UNAVAILABLE",
  SCANNED_UNAVAILABLE: "SCANNED_UNAVAILABLE",
  ERROR: "ERROR",
});

export const SCANNED_DOCUMENT_MESSAGE =
  "Este documento no contiene texto seleccionable. La lectura en voz alta no está disponible para archivos escaneados.";

/**
 * Selecciona una voz local en español.
 * Prioriza es-CO y rechaza taxativamente cualquier voz no local (localService !== true).
 */
export function selectLocalSpanishVoice(voices, preferredLang = "es-CO") {
  if (!Array.isArray(voices) || voices.length === 0) {
    return null;
  }

  // REGLA ESTRICTA DE PRIVACIDAD: Solo voces locales del dispositivo
  const localVoices = voices.filter((v) => v && v.localService === true);
  if (localVoices.length === 0) {
    return null;
  }

  const pref = (preferredLang || "es-CO").toLowerCase().replace("_", "-");

  // 1. Coincidencia exacta con el idioma preferido
  const exactMatch = localVoices.find(
    (v) => (v.lang || "").toLowerCase().replace("_", "-") === pref
  );
  if (exactMatch) return exactMatch;

  // 2. Coincidencia de prefijo con el idioma preferido (ej: es-co)
  const prefixMatch = localVoices.find((v) =>
    (v.lang || "").toLowerCase().replace("_", "-").startsWith(pref)
  );
  if (prefixMatch) return prefixMatch;

  // 3. Fallback a cualquier voz en español local
  const anySpanish = localVoices.find((v) => {
    const l = (v.lang || "").toLowerCase().replace("_", "-");
    return l === "es" || l.startsWith("es-");
  });

  return anySpanish || null;
}

/**
 * Extrae el texto legible de un objeto Page de PDF.js respetando el orden y saltos.
 */
export async function extractPageText(pdfPage) {
  if (!pdfPage || typeof pdfPage.getTextContent !== "function") {
    return "";
  }
  const content = await pdfPage.getTextContent();
  if (!content || !Array.isArray(content.items) || content.items.length === 0) {
    return "";
  }
  const parts = [];
  for (const item of content.items) {
    if (item && typeof item.str === "string") {
      parts.push(item.str);
      if (item.hasEOL) {
        parts.push("\n");
      } else {
        parts.push(" ");
      }
    }
  }
  return parts.join("").replace(/[ \t]+/g, " ").replace(/\n\s*\n/g, "\n\n").trim();
}

/**
 * Divide texto extenso en fragmentos razonables respetando oraciones y puntuación
 * para evitar interrupciones o cuelgues del motor de síntesis de voz en móviles.
 */
export function splitTextIntoChunks(text, maxChunkLen = 180) {
  if (!text || typeof text !== "string") return [];
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  if (!normalized) return [];

  const paragraphs = normalized.split(/\n+/);
  const chunks = [];

  for (const para of paragraphs) {
    const trimmedPara = para.trim();
    if (!trimmedPara) continue;

    const sentences =
      trimmedPara.match(/[^.?!;:—\n]+[.?!;:—\n]*|[^.?!;:—\n]+$/g) || [trimmedPara];
    let current = "";

    for (const rawSentence of sentences) {
      const sentence = rawSentence.trim();
      if (!sentence) continue;

      if ((current + " " + sentence).trim().length <= maxChunkLen) {
        current = current ? `${current} ${sentence}` : sentence;
      } else {
        if (current) {
          chunks.push(current);
          current = "";
        }
        if (sentence.length <= maxChunkLen) {
          current = sentence;
        } else {
          const words = sentence.split(/\s+/);
          let sub = "";
          for (const word of words) {
            if ((sub + " " + word).trim().length <= maxChunkLen) {
              sub = sub ? `${sub} ${word}` : word;
            } else {
              if (sub) chunks.push(sub);
              sub = word;
            }
          }
          if (sub) current = sub;
        }
      }
    }
    if (current) {
      chunks.push(current);
      current = "";
    }
  }

  return chunks;
}

export class PdfReadAloud {
  constructor(options = {}) {
    this.pdfDoc = options.pdfDoc || null;
    this.speechSynthesis =
      options.speechSynthesis !== undefined
        ? options.speechSynthesis
        : typeof window !== "undefined"
        ? window.speechSynthesis
        : null;
    this.SpeechSynthesisUtterance =
      options.SpeechSynthesisUtterance !== undefined
        ? options.SpeechSynthesisUtterance
        : typeof window !== "undefined"
        ? window.SpeechSynthesisUtterance
        : null;

    this.preferredLang = options.preferredLang || "es-CO";
    this.rate = Number(options.rate) || 1.0;
    this.onStateChange = options.onStateChange || (() => {});

    this.state = ReadAloudState.IDLE;
    this.currentPage = 1;
    this.currentChunkIndex = 0;
    this.totalPagesWithText = 0;
    this.activeVoice = null;
    this.isPaused = false;
    this.isStopped = false;
    this.currentUtterance = null;
    this._runSessionId = 0;

    if (this.pdfDoc) {
      this._checkVoiceAvailability();
    }
  }

  getStatus() {
    return {
      state: this.state,
      page: this.currentPage,
      totalPages: this.pdfDoc?.numPages || 0,
      rate: this.rate,
      voiceName: this.activeVoice?.name || null,
      voiceLang: this.activeVoice?.lang || null,
    };
  }

  setDocument(pdfDoc) {
    this.stop();
    this.pdfDoc = pdfDoc || null;
    this.currentPage = 1;
    this.currentChunkIndex = 0;
    this.totalPagesWithText = 0;
    if (this.pdfDoc) {
      this._checkVoiceAvailability();
    } else {
      this._setState(ReadAloudState.IDLE, "Sin documento cargado");
    }
  }

  setRate(rate) {
    const validRate = Number(rate);
    if (!Number.isNaN(validRate) && validRate > 0) {
      this.rate = validRate;
      if (this.currentUtterance) {
        try {
          this.currentUtterance.rate = this.rate;
        } catch (_e) {
          // Ignorar navegadores que impiden mutar el utterance activo
        }
      }
    }
  }

  _setState(state, message = "") {
    this.state = state;
    this.onStateChange({
      state: this.state,
      page: this.currentPage,
      totalPages: this.pdfDoc?.numPages || 0,
      message,
      voiceName: this.activeVoice?.name || null,
    });
  }

  _getAvailableVoices() {
    if (!this.speechSynthesis || typeof this.speechSynthesis.getVoices !== "function") {
      return [];
    }
    return this.speechSynthesis.getVoices() || [];
  }

  _checkVoiceAvailability() {
    if (!this.speechSynthesis || !this.SpeechSynthesisUtterance) {
      this._setState(
        ReadAloudState.UNAVAILABLE,
        "La síntesis de voz no está soportada en este navegador."
      );
      return false;
    }

    const voices = this._getAvailableVoices();
    this.activeVoice = selectLocalSpanishVoice(voices, this.preferredLang);

    if (!this.activeVoice) {
      // En algunos navegadores (Chrome/Edge/Android), getVoices() se carga de manera asíncrona
      if (voices.length === 0 && typeof this.speechSynthesis.addEventListener === "function") {
        this.speechSynthesis.addEventListener(
          "voiceschanged",
          () => {
            if (this.state === ReadAloudState.UNAVAILABLE || this.state === ReadAloudState.IDLE) {
              this._checkVoiceAvailability();
            }
          },
          { once: true }
        );
      }

      this._setState(
        ReadAloudState.UNAVAILABLE,
        "Lectura local no disponible. No se encontró una voz local en español disponible en este dispositivo."
      );
      return false;
    }

    this._setState(ReadAloudState.READY, "Listo para leer");
    return true;
  }

  async start() {
    if (this.state === ReadAloudState.PAUSED) {
      return this.resume();
    }

    if (!this.pdfDoc) {
      this._setState(ReadAloudState.UNAVAILABLE, "No hay documento cargado.");
      return;
    }

    if (!this._checkVoiceAvailability()) {
      return;
    }

    this.isStopped = false;
    this.isPaused = false;
    this.currentPage = 1;
    this.currentChunkIndex = 0;
    this.totalPagesWithText = 0;

    const sessionId = ++this._runSessionId;
    await this._runReadingSession(sessionId);
  }

  pause() {
    if (this.state !== ReadAloudState.READING && this.state !== ReadAloudState.PREPARING) {
      return;
    }
    this.isPaused = true;
    if (this._currentChunkResolver) {
      this._currentChunkResolver();
      this._currentChunkResolver = null;
    }
    if (this.speechSynthesis) {
      try {
        this.speechSynthesis.cancel();
      } catch (_e) {
        // Ignorar excepciones al cancelar síntesis
      }
    }
    this._setState(ReadAloudState.PAUSED, "Pausado");
  }

  async resume() {
    if (!this.isPaused && this.state !== ReadAloudState.PAUSED) {
      return;
    }
    this.isPaused = false;
    this.isStopped = false;
    const sessionId = ++this._runSessionId;
    await this._runReadingSession(sessionId);
  }

  stop() {
    this.isStopped = true;
    this.isPaused = false;
    this._runSessionId++;
    if (this._currentChunkResolver) {
      this._currentChunkResolver();
      this._currentChunkResolver = null;
    }
    if (this.speechSynthesis) {
      try {
        this.speechSynthesis.cancel();
      } catch (_e) {
        // Ignorar cancelaciones
      }
    }
    this.currentUtterance = null;
    this.currentPage = 1;
    this.currentChunkIndex = 0;
    this.totalPagesWithText = 0;
    this._setState(ReadAloudState.READY, "Listo para leer");
  }

  dispose() {
    this.stop();
    this.pdfDoc = null;
    this.onStateChange = () => {};
  }

  async _runReadingSession(sessionId) {
    const totalPages = this.pdfDoc?.numPages || 0;

    try {
      while (this.currentPage <= totalPages) {
        if (sessionId !== this._runSessionId || this.isStopped || this.isPaused) {
          return;
        }

        this._setState(
          ReadAloudState.PREPARING,
          `Preparando texto de la página ${this.currentPage} de ${totalPages}...`
        );

        let pageObj;
        try {
          pageObj = await this.pdfDoc.getPage(this.currentPage);
        } catch (pageError) {
          this._setState(
            ReadAloudState.ERROR,
            `Error al acceder a la página ${this.currentPage} del documento.`
          );
          return;
        }

        if (sessionId !== this._runSessionId || this.isStopped || this.isPaused) {
          return;
        }

        const pageText = await extractPageText(pageObj);
        if (sessionId !== this._runSessionId || this.isStopped || this.isPaused) {
          return;
        }

        const chunks = splitTextIntoChunks(pageText);

        if (chunks.length === 0) {
          // Página vacía o sin texto extraíble: avanzar a la siguiente
          this.currentPage += 1;
          this.currentChunkIndex = 0;
          continue;
        }

        this.totalPagesWithText += 1;

        // Leer secuencialmente los fragmentos de la página actual
        for (let i = this.currentChunkIndex; i < chunks.length; i++) {
          if (sessionId !== this._runSessionId || this.isStopped || this.isPaused) {
            return;
          }

          this.currentChunkIndex = i;
          const chunk = chunks[i];

          this._setState(
            ReadAloudState.READING,
            `Leyendo página ${this.currentPage} de ${totalPages}`
          );

          await this._speakChunk(chunk, sessionId);

          if (sessionId !== this._runSessionId || this.isStopped) {
            return;
          }
          if (this.isPaused) {
            return;
          }
          this.currentChunkIndex = i + 1;
        }

        // Página concluida con éxito: avanzar a la siguiente
        this.currentPage += 1;
        this.currentChunkIndex = 0;
      }

      if (sessionId !== this._runSessionId || this.isStopped || this.isPaused) {
        return;
      }

      // Si se recorrió todo el documento y no hubo texto en ninguna página
      if (this.totalPagesWithText === 0) {
        this._setState(ReadAloudState.SCANNED_UNAVAILABLE, SCANNED_DOCUMENT_MESSAGE);
      } else {
        this._setState(ReadAloudState.COMPLETED, "Lectura finalizada");
      }
    } catch (error) {
      if (sessionId === this._runSessionId && !this.isStopped && !this.isPaused) {
        this._setState(
          ReadAloudState.ERROR,
          error?.message || "Ocurrió un error durante la reproducción de voz."
        );
      }
    }
  }

  _speakChunk(text, sessionId) {
    return new Promise((resolve, reject) => {
      if (!this.SpeechSynthesisUtterance || !this.speechSynthesis) {
        resolve();
        return;
      }

      const utterance = new this.SpeechSynthesisUtterance(text);
      this.currentUtterance = utterance;

      if (this.activeVoice) {
        utterance.voice = this.activeVoice;
        utterance.lang = this.activeVoice.lang || this.preferredLang;
      } else {
        utterance.lang = this.preferredLang;
      }

      utterance.rate = this.rate;

      let hasResolved = false;
      const finish = () => {
        if (!hasResolved) {
          hasResolved = true;
          this._currentChunkResolver = null;
          this.currentUtterance = null;
          resolve();
        }
      };
      this._currentChunkResolver = finish;

      utterance.onend = () => {
        finish();
      };

      utterance.onerror = (event) => {
        if (
          sessionId !== this._runSessionId ||
          this.isStopped ||
          this.isPaused ||
          event?.error === "canceled" ||
          event?.error === "interrupted"
        ) {
          finish();
        } else {
          hasResolved = true;
          this.currentUtterance = null;
          reject(new Error(`Error de síntesis de voz: ${event?.error || "desconocido"}`));
        }
      };

      try {
        this.speechSynthesis.speak(utterance);
      } catch (err) {
        hasResolved = true;
        this.currentUtterance = null;
        reject(err);
      }
    });
  }
}
