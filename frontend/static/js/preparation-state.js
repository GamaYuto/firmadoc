export function normalizeUser(value, fallback = "") {
  return String(value || fallback).trim().toLowerCase();
}

export function buildDraftPayload(positions) {
  const participantMap = new Map();
  const cleanPositions = positions.map((position) => {
    const usrid = normalizeUser(position.usrid);
    const orden = Number(position.orden || 1);
    if (!usrid) {
      throw new Error("Cada posicion debe tener un firmante.");
    }
    const previous = participantMap.get(usrid);
    if (previous && previous.orden !== orden) {
      throw new Error("Un mismo firmante no puede tener ordenes distintos en este borrador.");
    }
    participantMap.set(usrid, { usrid, orden, obliga: true });
    return {
      pagina: Number(position.pagina),
      posx: Number(position.posx),
      posy: Number(position.posy),
      ancho: Number(position.ancho),
      alto: Number(position.alto),
      rotaci: Number(position.rotaci || 0),
      orden,
      tipfir: position.tipfir,
      usrid,
    };
  });

  if (!cleanPositions.length) {
    throw new Error("Dibuje al menos un rectangulo antes de guardar.");
  }

  return {
    participants: Array.from(participantMap.values()).sort((left, right) => left.orden - right.orden),
    positions: cleanPositions,
  };
}

export function hasDirtyState(state) {
  return Boolean(state?.dirty || state?.positions?.some((position) => position.dirty === true || position.saved === false));
}

