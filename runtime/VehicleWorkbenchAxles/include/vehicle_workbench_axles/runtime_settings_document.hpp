#pragma once

#include "vehicle_workbench_axles/configuration.hpp"

#include <optional>
#include <string>
#include <vector>

namespace vwa {

// Emit the portable schema-2 runtime settings document consumed by both Story
// hosts. Schema-1 values are migrated without changing where they resolve:
// their historical VehicleWorkbenchAxles/ base is made explicit in the new
// GTA-root-relative value.
std::optional<std::string> SerializePortableRuntimeSettingsJson(
    const RuntimeSettings& settings, std::vector<ValidationIssue>& issues,
    const std::string& source_name = "runtime.json");

} // namespace vwa
