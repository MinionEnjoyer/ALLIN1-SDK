#pragma once

#include "vehicle_workbench_axles/types.hpp"

#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace vwa {

inline constexpr std::uint32_t kAxleSchemaVersion = 1;
inline constexpr std::uint32_t kRuntimeSettingsSchemaVersion = 1;
inline constexpr std::uint8_t kSteeredBit = 0x08;
inline constexpr std::uint8_t kDrivenBit = 0x10;

struct AxleDefinition {
    std::uint32_t order{0};
    std::string role;
    std::string left_bone;
    std::string right_bone;
    bool steered{false};
    bool powered{false};
};

struct AxleConfiguration {
    std::uint32_t schema_version{kAxleSchemaVersion};
    std::string configuration_id;
    std::string model_name;
    std::uint32_t model_hash{0};
    std::uint32_t expected_wheel_count{0};
    std::string minimum_runtime_version;
    // Canonical bone name -> target-exported physical wheel index.  Runtime
    // code never derives an index from array order.
    std::map<std::string, std::uint32_t> wheel_index_map;
    std::vector<AxleDefinition> axles;
    bool story_legacy{true};
    bool story_enhanced{true};
};

struct RuntimeSettings {
    std::uint32_t schema_version{kRuntimeSettingsSchemaVersion};
    std::uint32_t discovery_interval_ms{250};
    std::uint32_t recovery_interval_ms{2000};
    bool restore_on_unload{true};
    std::string log_file{"logs/VehicleWorkbenchAxles.log"};
};

struct ConfigurationCatalog {
    std::unordered_map<std::uint32_t, AxleConfiguration> active;
    std::vector<ValidationIssue> issues;
    std::size_t files_seen{0};
};

std::vector<ValidationIssue>
ValidateConfiguration(const AxleConfiguration& configuration,
                      const std::string& runtime_version,
                      const std::string& source_name = {});

std::optional<AxleConfiguration>
ParseConfigurationJson(const std::string& json_text,
                       const std::string& runtime_version,
                       std::vector<ValidationIssue>& issues,
                       const std::string& source_name = {});

ConfigurationCatalog
LoadConfigurationDirectory(const std::filesystem::path& directory,
                           const std::string& runtime_version);

std::optional<RuntimeSettings>
ParseRuntimeSettingsJson(const std::string& json_text,
                         std::vector<ValidationIssue>& issues,
                         const std::string& source_name = "runtime.json");

bool IsCanonicalPair(const std::string& left_bone,
                     const std::string& right_bone) noexcept;

} // namespace vwa
