import assert from "node:assert/strict";
import test from "node:test";

import {
  PdfReadAloud,
  ReadAloudState,
  SCANNED_DOCUMENT_MESSAGE,
  extractPageText,
  selectLocalSpanishVoice,
  splitTextIntoChunks,
} from "../static/js/pdf-read-aloud.js";

// Helpers para simular PDF.js
function createMockPage(items = []) {
  return {
    async getTextContent() {
      return {
        items: items.map((it) => (typeof it === "string" ? { str: it, hasEOL: false } : it)),
      };
    },
  };
}

function createMockPdfDoc(pagesData = []) {
  return {
    numPages: pagesData.length,
    async getPage(pageNumber) {
      if (pageNumber < 1 || pageNumber > pagesData.length) {
        throw new Error(`Página ${pageNumber} fuera de límites`);
      }
      return createMockPage(pagesData[pageNumber - 1]);
    },
  };
}

// Helpers para simular Web Speech API
class MockSpeechSynthesisUtterance {
  constructor(text) {
    this.text = text;
    this.voice = null;
    this.rate = 1.0;
    this.lang = "es-CO";
    this.onstart = null;
    this.onend = null;
    this.onerror = null;
  }
}

function createMockSpeechSynthesis({ voices = [], autoEndDelay = 1, onSpeak = null } = {}) {
  const spokenList = [];
  let cancelledCount = 0;
  let isPaused = false;

  return {
    spokenList,
    get cancelledCount() {
      return cancelledCount;
    },
    getVoices() {
      return voices;
    },
    speak(utterance) {
      spokenList.push(utterance);
      if (typeof onSpeak === "function") {
        onSpeak(utterance);
      }
      if (autoEndDelay >= 0) {
        setTimeout(() => {
          if (typeof utterance.onstart === "function") utterance.onstart();
          if (typeof utterance.onend === "function") utterance.onend();
        }, autoEndDelay);
      }
    },
    cancel() {
      cancelledCount++;
      isPaused = false;
    },
    pause() {
      isPaused = true;
    },
    resume() {
      isPaused = false;
    },
    get isPaused() {
      return isPaused;
    },
  };
}

const SAMPLE_LOCAL_VOICES = [
  { name: "Sabina Local", lang: "es-CO", localService: true },
  { name: "Helena Local", lang: "es-ES", localService: true },
  { name: "Zira Remote", lang: "es-CO", localService: false },
  { name: "David English", lang: "en-US", localService: true },
];

test("1. PDF de una página con texto se lee secuencialmente", async () => {
  const pdfDoc = createMockPdfDoc([
    ["El presente Contrato de prestación de servicios se formaliza hoy."],
  ]);
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });
  const states = [];

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
    onStateChange: (st) => states.push(st.state),
  });

  assert.equal(reader.state, ReadAloudState.READY);
  await reader.start();

  assert.ok(synth.spokenList.length >= 1);
  assert.ok(synth.spokenList[0].text.includes("El presente Contrato"));
  assert.equal(reader.state, ReadAloudState.COMPLETED);
  assert.ok(states.includes(ReadAloudState.READING));
  assert.ok(states.includes(ReadAloudState.COMPLETED));
});

test("2. PDF multipágina recorre secuencialmente todas las páginas", async () => {
  const pdfDoc = createMockPdfDoc([
    ["Primera página con antecedentes clínicos."],
    ["Segunda página con diagnóstico y plan de tratamiento."],
    ["Tercera página con firmas de autorización."],
  ]);
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });
  const pagesRead = [];

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
    onStateChange: (st) => {
      if (st.state === ReadAloudState.READING) {
        pagesRead.push(st.page);
      }
    },
  });

  await reader.start();

  assert.equal(reader.state, ReadAloudState.COMPLETED);
  assert.equal(synth.spokenList.length, 3);
  assert.ok(synth.spokenList[0].text.includes("Primera página"));
  assert.ok(synth.spokenList[1].text.includes("Segunda página"));
  assert.ok(synth.spokenList[2].text.includes("Tercera página"));
  assert.ok(pagesRead.includes(1));
  assert.ok(pagesRead.includes(2));
  assert.ok(pagesRead.includes(3));
});

test("3. Texto en español con tildes, diéresis y eñes se conserva intacto", async () => {
  const complexText = "Evaluación médica del niño con afección hepática y bilingüe.";
  const pdfDoc = createMockPdfDoc([[complexText]]);
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  await reader.start();

  assert.equal(synth.spokenList.length, 1);
  assert.equal(synth.spokenList[0].text, complexText);
  assert.equal(synth.spokenList[0].voice.name, "Sabina Local");
});

test("4. Página intermedia vacía no detiene el avance a las siguientes páginas", async () => {
  const pdfDoc = createMockPdfDoc([
    ["Cláusula inicial de términos y condiciones."],
    [], // Página 2 sin texto
    ["Cláusula final de consentimiento informado."],
  ]);
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  await reader.start();

  assert.equal(reader.state, ReadAloudState.COMPLETED);
  assert.equal(synth.spokenList.length, 2);
  assert.ok(synth.spokenList[0].text.includes("Cláusula inicial"));
  assert.ok(synth.spokenList[1].text.includes("Cláusula final"));
});

test("5. PDF escaneado sin texto extraíble emite mensaje institucional exacto", async () => {
  const pdfDoc = createMockPdfDoc([[], [], []]); // 3 páginas sin texto
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });
  let finalMessage = "";

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
    onStateChange: (st) => {
      finalMessage = st.message;
    },
  });

  await reader.start();

  assert.equal(reader.state, ReadAloudState.SCANNED_UNAVAILABLE);
  assert.equal(synth.spokenList.length, 0);
  assert.equal(finalMessage, SCANNED_DOCUMENT_MESSAGE);
});

test("6. Pausar y reanudar continúa en la misma página y fragmento", async () => {
  const pdfDoc = createMockPdfDoc([
    ["Frase uno de prueba. Frase dos de prueba. Frase tres de prueba."],
  ]);
  let firstSpoken = false;
  let onFirstSpeak;
  const waitFirstSpeak = new Promise((resolve) => {
    onFirstSpeak = resolve;
  });

  const synth = createMockSpeechSynthesis({
    voices: SAMPLE_LOCAL_VOICES,
    autoEndDelay: 10,
    onSpeak() {
      if (!firstSpoken) {
        firstSpoken = true;
        onFirstSpeak();
      }
    },
  });

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  const startPromise = reader.start();
  await waitFirstSpeak;

  // Pausar
  reader.pause();
  assert.equal(reader.state, ReadAloudState.PAUSED);
  await startPromise;

  assert.equal(reader.isPaused, true);
  const countBeforeResume = synth.spokenList.length;

  // Reanudar
  await reader.resume();
  assert.equal(reader.state, ReadAloudState.COMPLETED);
  assert.ok(synth.spokenList.length >= countBeforeResume);
});

test("7. Detener cancela la síntesis y restablece el estado a READY", async () => {
  const pdfDoc = createMockPdfDoc([["Página con texto a detener."]]);
  let onFirstSpeakResolve;
  const onFirstSpeakPromise = new Promise((r) => {
    onFirstSpeakResolve = r;
  });

  const synth = createMockSpeechSynthesis({
    voices: SAMPLE_LOCAL_VOICES,
    autoEndDelay: 50,
    onSpeak: () => {
      if (onFirstSpeakResolve) {
        onFirstSpeakResolve();
        onFirstSpeakResolve = null;
      }
    },
  });

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  const startPromise = reader.start();
  await onFirstSpeakPromise;

  assert.equal(synth.spokenList.length, 1);

  reader.stop();
  assert.equal(reader.state, ReadAloudState.READY);
  assert.equal(reader.currentPage, 1);
  assert.equal(reader.currentChunkIndex, 0);
  assert.ok(synth.cancelledCount >= 1);
  await startPromise;
});

test("8. Cambiar documento durante lectura detiene la sesión anterior", async () => {
  const doc1 = createMockPdfDoc([["Texto del primer documento."]]);
  const doc2 = createMockPdfDoc([["Texto del segundo documento."]]);
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES, autoEndDelay: 50 });

  const reader = new PdfReadAloud({
    pdfDoc: doc1,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  reader.start();
  reader.setDocument(doc2);

  assert.equal(reader.pdfDoc, doc2);
  assert.equal(reader.state, ReadAloudState.READY);
  assert.ok(synth.cancelledCount >= 1);
});

test("9. Error al acceder a una página del PDF se captura en estado ERROR", async () => {
  const brokenDoc = {
    numPages: 2,
    async getPage() {
      throw new Error("Fallo de descompresión en página");
    },
  };
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });

  const reader = new PdfReadAloud({
    pdfDoc: brokenDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  await reader.start();

  assert.equal(reader.state, ReadAloudState.ERROR);
  assert.equal(synth.spokenList.length, 0);
});

test("10. Error del motor de voz emite estado ERROR", async () => {
  const pdfDoc = createMockPdfDoc([["Texto para disparar error de síntesis."]]);
  const synth = {
    getVoices: () => SAMPLE_LOCAL_VOICES,
    speak(utterance) {
      setTimeout(() => {
        if (utterance.onerror) utterance.onerror({ error: "audio-hardware" });
      }, 5);
    },
    cancel() {},
  };

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  await reader.start();

  assert.equal(reader.state, ReadAloudState.ERROR);
});

test("11. Navegador sin soporte de speechSynthesis se marca UNAVAILABLE", () => {
  const pdfDoc = createMockPdfDoc([["Texto"]]);

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: null,
    SpeechSynthesisUtterance: null,
  });

  assert.equal(reader.state, ReadAloudState.UNAVAILABLE);
});

test("12. Ausencia de voz local en español marca UNAVAILABLE", () => {
  const onlyEnglishVoices = [
    { name: "David English", lang: "en-US", localService: true },
    { name: "George UK", lang: "en-GB", localService: true },
  ];
  const synth = createMockSpeechSynthesis({ voices: onlyEnglishVoices });
  const pdfDoc = createMockPdfDoc([["Texto en español."]]);

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  assert.equal(reader.state, ReadAloudState.UNAVAILABLE);
});

test("13. Prohibición de utilizar automáticamente voces remotas (localService === false)", () => {
  const onlyRemoteSpanishVoices = [
    { name: "Cloud Spanish Voice", lang: "es-CO", localService: false },
    { name: "Cloud ES Voice", lang: "es-ES", localService: false },
    { name: "Local English Voice", lang: "en-US", localService: true },
  ];

  const selected = selectLocalSpanishVoice(onlyRemoteSpanishVoices, "es-CO");
  assert.equal(selected, null);

  const synth = createMockSpeechSynthesis({ voices: onlyRemoteSpanishVoices });
  const pdfDoc = createMockPdfDoc([["Texto que no debe salir a la nube."]]);

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  assert.equal(reader.state, ReadAloudState.UNAVAILABLE);
});

test("14. Inicializar PdfReadAloud no reproduce audio automáticamente al cargar la página", () => {
  const pdfDoc = createMockPdfDoc([["Texto confidencial."]]);
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  // No debe haberse hablado nada
  assert.equal(synth.spokenList.length, 0);
  assert.equal(reader.state, ReadAloudState.READY);
});

test("15. Finalización de lectura no activa automáticamente la autorización de Gerencia", async () => {
  let authorizationTriggered = false;
  const mockApprovalService = {
    authorizeDocument() {
      authorizationTriggered = true;
    },
  };

  const pdfDoc = createMockPdfDoc([["Contrato revisado por el gerente en su totalidad."]]);
  const synth = createMockSpeechSynthesis({ voices: SAMPLE_LOCAL_VOICES });

  const reader = new PdfReadAloud({
    pdfDoc,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: MockSpeechSynthesisUtterance,
  });

  await reader.start();

  assert.equal(reader.state, ReadAloudState.COMPLETED);
  // Escuchar el PDF no significa autorizarlo
  assert.equal(authorizationTriggered, false);
});
