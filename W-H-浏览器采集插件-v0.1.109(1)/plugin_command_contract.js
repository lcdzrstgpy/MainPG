(function exposePluginCommandContract(root) {
  "use strict";

  function normalizePolledCommands(payload) {
    const commands = Array.isArray(payload) ? payload : (payload && payload.commands) || [];
    if (!Array.isArray(commands)) return [];
    return commands
      .filter((command) => command && typeof command === "object")
      .map((command) => ({ ...command, id: command.id ?? command.command_id }))
      .filter((command) => command.id !== undefined && command.id !== null && command.id !== "");
  }

  async function dispatchPolledCommands(payload, execute) {
    for (const command of normalizePolledCommands(payload)) await execute(command);
  }

  const contract = { normalizePolledCommands, dispatchPolledCommands };
  root.WorkbenchPluginCommandContract = contract;
  if (typeof module !== "undefined" && module.exports) module.exports = contract;
})(typeof globalThis !== "undefined" ? globalThis : self);
