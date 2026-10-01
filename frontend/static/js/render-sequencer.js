export class RenderSequencer {
  constructor() {
    this.generation = 0;
    this.current = Promise.resolve();
  }

  schedule(task) {
    const generation = ++this.generation;
    this.current = this.current
      .catch(() => undefined)
      .then(() => task({ generation, isCurrent: () => generation === this.generation }));
    return this.current;
  }
}

