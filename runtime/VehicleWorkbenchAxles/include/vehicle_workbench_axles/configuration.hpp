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

inline constexpr std::uint32_t kLegacyAxleSchemaVersion = 1;
inline constexpr std::uint32_t kAxleSchemaVersion = 2;
inline constexpr std::uint32_t kRuntimeSettingsSchemaVersion = 1;
inline constexpr std::uint8_t kSteeredBit = 0x08;
inline constexpr std::uint8_t kDrivenBit = 0x10;
// Signed steering gain is normalized to the authored front-axle steering
// input.  Positive values steer in phase, negative values counter-steer, and
// zero holds the physical axle neutral.  A missing value retains the schema-1
// boolean-only behavior (+1 for steered axles, 0 for non-steered axles).
inline constexpr double kMinimumSteeringGain = -1.0;
inline constexpr double kMaximumSteeringGain = 1.0;
inline constexpr double kSteeringGainEpsilon = 1.0e-9;
inline constexpr const char* kSignedSteeringMinimumRuntime = "2.0.0";

struct SteeringCalculationEvidence {
    std::string mode;
    std::uint32_t algorithm_version{0};
    std::string bone_position_sha256;
    std::optional<double> pivot_longitudinal_position;
    std::string pivot_source;
    std::vector<std::uint32_t> pivot_axle_orders;
    std::optional<std::uint32_t> reference_axle_order;
    std::optional<double> reference_lock_degrees;
    std::optional<double> pair_position_tolerance;
    std::optional<double> position_epsilon;
};

struct AxleDefinition {
    std::uint32_t order{0};
    std::string role;
    std::string left_bone;
    std::string right_bone;
    bool steered{false};
    bool powered{false};
    std::optional<double> steering_gain;
};

struct AxleConfiguration {
    std::uint32_t schema_version{kLegacyAxleSchemaVersion};
    std::string configuration_id;
    std::string model_name;
    std::uint32_t model_hash{0};
    std::uint32_t expected_wheel_count{0};
    std::string minimum_runtime_version;
    // Canonical bone name -> target-exported physical wheel index.  Runtime
    // code never derives an index from array order.
    std::map<std::string, std::uint32_t> wheel_index_map;
    std::vector<AxleDefinition> axles;
    std::optional<SteeringCalculationEvidence> steering_calculation;
    // A configuration is never enabled for an edition by omission.  Parsing
    // must observe an exact, recognized Story compatibility key set to true.
    bool story_legacy{false};
    bool story_enhanced{false};
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
