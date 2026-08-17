export type WorkspaceTabScrollPosition = {
  windowY: number;
  contentY: number;
};

export class WorkspaceTabScrollStore {
  private readonly positions = new Map<string, WorkspaceTabScrollPosition>();

  save(tabKey: string, position: WorkspaceTabScrollPosition) {
    this.positions.set(tabKey, { ...position });
  }

  restore(tabKey: string) {
    const position = this.positions.get(tabKey);
    return position ? { ...position } : undefined;
  }

  remove(tabKey: string) {
    this.positions.delete(tabKey);
  }
}
