/**
 * Deterministic generation fence for notification refreshes.
 * Hiding/stopping invalidates a pending generation; a late response can never
 * restore notifications from an older workspace-visible epoch.
 */
export class DimensionNotificationRefreshFence<T> {
  #generation = 0;
  #inFlight = false;
  #visible = true;
  #stopped = false;

  begin(): number | null {
    if (this.#stopped || !this.#visible || this.#inFlight) return null;
    this.#inFlight = true;
    this.#generation += 1;
    return this.#generation;
  }

  succeed(generation: number, value: T, apply: (value: T) => void): boolean {
    if (this.#stopped || !this.#visible || generation !== this.#generation) return false;
    this.#inFlight = false;
    apply(value);
    return true;
  }

  fail(generation: number): boolean {
    if (this.#stopped || generation !== this.#generation) return false;
    this.#inFlight = false;
    // A later success callback from the failed request is stale by definition.
    this.#generation += 1;
    return true;
  }

  setVisible(visible: boolean): void {
    if (this.#visible === visible || this.#stopped) return;
    this.#visible = visible;
    if (!visible) {
      this.#generation += 1;
      this.#inFlight = false;
    }
  }

  stop(): void {
    this.#stopped = true;
    this.#generation += 1;
    this.#inFlight = false;
  }
}
