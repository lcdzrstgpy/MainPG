export type AiCreationMode = "chat" | "generate" | "edit";

export type AiModel = {
  id: string;
  name: string;
  description: string;
  capabilities: AiCreationMode[];
  acceptsImageInput?: boolean;
};

export type AiCreationTemplate = {
  id: string;
  label: string;
  description: string;
  icon: string;
  mode: Extract<AiCreationMode, "generate" | "edit">;
  prompt: string;
};

export type AiConversation = {
  id: string;
  title: string;
  mode: AiCreationMode;
  preview: string;
  time: string;
  isPinned: boolean;
  updatedAt: string;
};

export type AiMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  uploadedImageUrl?: string;
  uploadedDocumentName?: string;
  generatedImageUrls?: string[];
  generatedImageGroups?: Array<{ label: string; imageUrls: string[] }>;
};
