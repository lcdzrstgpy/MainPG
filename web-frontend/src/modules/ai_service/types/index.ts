export type AiCreationMode = "chat" | "generate" | "edit";

export type AiModel = {
  id: string;
  name: string;
  description: string;
  capabilities: AiCreationMode[];
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
  preview: string;
  time: string;
};

export type AiMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  uploadedImageUrl?: string;
  uploadedDocumentName?: string;
  generatedImageUrls?: string[];
};
