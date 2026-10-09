export type ComposerDraft = {
  prompt: string;
  imageUrl?: string;
  imageName: string;
  assetId?: string;
};

export function consumeComposerDraft(draft: ComposerDraft) {
  return {
    submitted: draft,
    next: {
      prompt: "",
      imageUrl: undefined,
      imageName: "",
      assetId: undefined,
    },
  };
}
