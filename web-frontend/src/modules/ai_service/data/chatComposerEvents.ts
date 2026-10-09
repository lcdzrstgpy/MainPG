type ClipboardFileItem = Pick<DataTransferItem, "kind" | "type" | "getAsFile">;

export function shouldSendOnEnter(event: Pick<KeyboardEvent, "key" | "shiftKey" | "isComposing">) {
  return event.key === "Enter" && !event.shiftKey && !event.isComposing;
}

export function pastedImageFile(items: Iterable<ClipboardFileItem>, timestamp = Date.now()) {
  const imageItem = Array.from(items).find((item) => item.kind === "file" && item.type.startsWith("image/"));
  const source = imageItem?.getAsFile();
  if (!source) return undefined;
  const extension = source.type.split("/", 2)[1] || "png";
  return new File([source], `pasted-image-${timestamp}.${extension}`, { type: source.type });
}
