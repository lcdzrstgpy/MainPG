const bridgeInput = document.querySelector("#bridge-url");
const pairingInput = document.querySelector("#pairing-code");
const status = document.querySelector("#status");

chrome.storage.local.get("priceVerificationSession").then(({ priceVerificationSession: session }) => {
  if (session && session.bridgeBaseUrl) bridgeInput.value = session.bridgeBaseUrl;
});

document.querySelector("#connect").addEventListener("click", async () => {
  status.textContent = "Connecting…";
  const response = await chrome.runtime.sendMessage({ type: "connect", bridgeBaseUrl: bridgeInput.value, pairingCode: pairingInput.value });
  pairingInput.value = "";
  status.textContent = response && response.ok ? "Connected" : `Connection failed: ${(response && response.error) || "unknown error"}`;
});
