import type { HelpTopic } from "./types";

// Development-only stress fixture; production topics still come from Python.
export const helpPreviewTopics: HelpTopic[] = [
  "Getting started", "Legacy and Enhanced installations", "Product workspace evidence",
  "SDK toolchain setup", "Package Linker", "Asset previews", "Vehicle authoring",
  "Axles and transmissions", "Weapon authoring", "Custom scope offsets",
  "Models and materials", "Texture workspaces", "RPF archive inspection",
  "GXT2 game text", "Change sets and packaging", "Transaction receipts and rollback",
  "Quick Import", "Package recipes", "SDK Console", "Qwen assistant",
].map((title, index) => ({
  key: index === 0 ? "getting-started" : `help-preview-${index}`,
  category: index < 4 ? "Start here" : "SDK reference",
  title,
  summary: `Read the ${title.toLocaleLowerCase()} workflow and review its evidence.`,
  body: Array.from({ length: index === 0 ? 18 : 3 }, (_, paragraph) =>
    `Preview section ${paragraph + 1}. This is a read-only layout fixture for ${title.toLocaleLowerCase()}. ` +
    "The topic list and article should scroll independently, with search and the workspace heading remaining visible. " +
    "No package, archive, or game files are changed by this preview.").join("\n"),
  keywords: ["preview", index === 19 ? "assistant" : "reference"],
}));
