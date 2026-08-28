#pragma once

#include "vehicle_workbench_axles/types.hpp"

#include <cstdint>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace vwa {

inline constexpr std::uint32_t kLegacyAxleSchemaVersion = 1;
inline constexpr std::uint32_t kSignedSteeringAxleSchemaVersion = 2;
inline constexpr std::uint32_t kAxleSupportAxleSchemaVersion = 3;
inline constexpr std::uint32_t kAxleSchemaVersion = 4;
inline constexpr std::uint32_t kRuntimeSettingsSchemaVersion = 2;
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
inline constexpr const char* kIntentionalLayoutMinimumRuntime = "2.1.0";
inline constexpr const char* kAxleSupportMinimumRuntime = "3.0.0";
inline constexpr const char* kSteeringPolarityMinimumRuntime = "4.0.0";
inline constexpr const char* kRuntimeGeometryMinimumRuntime = "4.1.0";
inline constexpr double kMinimumSupportWeight = 0.75;
inline constexpr double kMaximumSupportWeight = 1.25;

struct SteeringCalculationEvidence {
    std::string mode;
    std::uint32_t algorithm_version{0};
    std::string bone_position_sha256;
    // When enabled, the runtime discards the exported signed gains and solves
    // them again from validated, vehicle-local wheel positions.  Manual mode
    // never permits runtime recomputation.
    bool runtime_recompute{false};
    std::string reference_selection;
    std::optional<double> pivot_longitudinal_position;
    std::string pivot_source;
    std::vector<std::uint32_t> pivot_axle_orders;
    std::optional<std::uint32_t> reference_axle_order;
    std::optional<double> reference_lock_degrees;
    std::optional<double> pair_position_tolerance;
    std::optional<double> position_epsilon;
    // Required when signed/scaled steering is calculated against an
    // intentional noncanonical physical layout. This binds the evidence to
    // the exact front-to-rear override order it was calculated from.
    std::vector<std::pair<std::string, std::string>> physical_bone_pairs;
};

struct IntentionalLayoutOverride {
    std::string mode;
    std::vector<std::pair<std::string, std::string>> physical_bone_pairs;
    std::string bone_position_sha256;
    std::string reason;
};

struct AxleSuspension {
    // Relative support bias for this physical axle. Runtime application
    // normalizes all weights against the vehicle's original total StaticForce,
    // so authoring bias never silently adds or removes total support.
    double support_weight{1.0};
};

struct AxleDefinition {
    std::uint32_t order{0};
    std::string role;
    std::string left_bone;
    std::string right_bone;
    bool steered{false};
    bool powered{false};
    std::optional<double> steering_gain;
    std::optional<AxleSuspension> suspension;
};

struct AxleConfiguration {
    std::uint32_t schema_version{kLegacyAxleSchemaVersion};
    std::string configuration_id;
    std::string model_name;
    std::uint32_t model_hash{0};
    std::uint32_t expected_wheel_count{0};
    std::string minimum_runtime_version;
    // Per-axle values are immutable base gains. Runtime multiplies them once
    // by this vehicle-level command polarity when planning wheel writes.
    std::string steering_command_polarity{"normal"};
    // Canonical bone name -> target-exported physical wheel index.  Runtime
    // code never derives an index from array order.
    std::map<std::string, std::uint32_t> wheel_index_map;
    std::vector<AxleDefinition> axles;
    std::optional<SteeringCalculationEvidence> steering_calculation;
    // Explicit, SDK-validated exception for vehicles which reuse GTA's two
    // visual wheel families in a noncanonical physical order. Runtime wheel
    // indices remain independently mapped by canonical bone name.
    std::optional<IntentionalLayoutOverride> intentional_layout_override;
    // A configuration is never enabled for an edition by omission.  Parsing
    // must observe an exact, recognized Story compatibility key set to true.
    bool story_legacy{false};
    bool story_enhanced{false};
};

struct RuntimeSettings {
    std::uint32_t schema_version{kRuntimeSettingsSchemaVersion};
    // The controller is installed enabled by default, but an owner can stop
    // all discovery/profile/wheel work without moving or renaming the ASI.
    bool enabled{true};
    std::uint32_t discovery_interval_ms{250};
    std::uint32_t recovery_interval_ms{2000};
    bool restore_on_unload{true};
    // Both paths are relative to the GTA installation directory (the folder
    // containing this root-level ASI). This permits a vehicle pack to keep its
    // sidecars and log alongside its existing scripts without moving the ASI.
    std::string configuration_directory{"VehicleWorkbenchAxles/configs"};
    std::string log_file{
        "VehicleWorkbenchAxles/logs/VehicleWorkbenchAxles.log"};
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
