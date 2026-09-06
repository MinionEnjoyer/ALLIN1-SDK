import capabilities from "./formatCapabilities.json";

const nativeExtensions = new Set(capabilities.formats.filter(format => format.nativeXmlEdit).map(format => format.suffix));
export const isNativeResource = (name: string) => nativeExtensions.has(name.slice(name.lastIndexOf(".")).toLowerCase());
