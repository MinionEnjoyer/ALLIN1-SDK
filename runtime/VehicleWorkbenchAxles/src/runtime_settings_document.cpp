#include "vehicle_workbench_axles/runtime_settings_document.hpp"

#include <algorithm>
#include <sstream>

namespace vwa {
namespace {

std::string PortablePath(std::string value) {
    std::replace(value.begin(), value.end(), '\\', '/');
    return value;
}

std::string EscapeJsonString(const std::string& value) {
    std::ostringstream output;
    for (const unsigned char character : value) {
        switch (character) {
        case '"':
            output << "\\\"";
            break;
        case '\\':
            output << "\\\\";
            break;
        case '\b':
            output << "\\b";
            break;
        case '\f':
            output << "\\f";
            break;
        case '\n':
            output << "\\n";
            break;
        case '\r':
            output << "\\r";
            break;
        case '\t':
            output << "\\t";
            break;
        default:
            if (character < 0x20U) {
                static constexpr char kHex[] = "0123456789abcdef";
                output << "\\u00" << kHex[(character >> 4U) & 0x0fU]
                       << kHex[character & 0x0fU];
            } else {
                output << static_cast<char>(character);
            }
            break;
        }
    }
    return output.str();
}

} // namespace

std::optional<std::string>
SerializePortableRuntimeSettingsJson(const RuntimeSettings& settings,
                                     std::vector<ValidationIssue>& issues,
                                     const std::string& source_name) {
    if (settings.schema_version < 1U ||
        settings.schema_version > kRuntimeSettingsSchemaVersion) {
        issues.push_back({"unsupported-runtime-settings-schema",
                          "runtime settings require schema version 1 or 2",
                          source_name, true});
        return std::nullopt;
    }
    RuntimeSettings portable = settings;
    portable.configuration_directory =
        PortablePath(portable.configuration_directory);
    portable.log_file = PortablePath(portable.log_file);
    if (portable.schema_version == 1U) {
        portable.configuration_directory =
            "VehicleWorkbenchAxles/" + portable.configuration_directory;
        portable.log_file = "VehicleWorkbenchAxles/" + portable.log_file;
    }
    portable.schema_version = kRuntimeSettingsSchemaVersion;

    std::ostringstream output;
    output << "{\n"
           << "  \"schemaVersion\": " << portable.schema_version << ",\n"
           << "  \"enabled\": " << (portable.enabled ? "true" : "false")
           << ",\n"
           << "  \"discoveryIntervalMs\": " << portable.discovery_interval_ms
           << ",\n"
           << "  \"recoveryIntervalMs\": " << portable.recovery_interval_ms
           << ",\n"
           << "  \"restoreOnUnload\": "
           << (portable.restore_on_unload ? "true" : "false") << ",\n"
           << "  \"configurationDirectory\": \""
           << EscapeJsonString(portable.configuration_directory) << "\",\n"
           << "  \"logFile\": \"" << EscapeJsonString(portable.log_file)
           << "\"\n"
           << "}\n";

    const auto document = output.str();
    const auto first_issue = issues.size();
    const auto parsed = ParseRuntimeSettingsJson(document, issues, source_name);
    if (!parsed.has_value())
        return std::nullopt;
    if (std::any_of(issues.begin() + static_cast<std::ptrdiff_t>(first_issue),
                    issues.end(),
                    [](const ValidationIssue& issue) { return issue.fatal; })) {
        return std::nullopt;
    }
    return document;
}

} // namespace vwa
