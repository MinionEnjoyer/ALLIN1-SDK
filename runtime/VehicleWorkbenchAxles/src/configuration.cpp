#include "vehicle_workbench_axles/configuration.hpp"

#include "json.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>
#include <tuple>

namespace vwa {

namespace {

using CanonicalPair = std::pair<const char*, const char*>;

constexpr std::array<CanonicalPair, 5> kCanonicalPairs{{
    {"wheel_lf", "wheel_rf"},
    {"wheel_lm1", "wheel_rm1"},
    {"wheel_lm2", "wheel_rm2"},
    {"wheel_lm3", "wheel_rm3"},
    {"wheel_lr", "wheel_rr"},
}};

void AddIssue(std::vector<ValidationIssue>& issues, std::string code,
              std::string message, const std::string& source_name,
              bool fatal = true) {
    issues.push_back(
        {std::move(code), std::move(message), source_name, fatal});
}

const json::Value& Required(const json::Value& object,
                            const std::string& key) {
    const auto* value = object.Find(key);
    if (value == nullptr) {
        throw json::Error("missing required field '" + key + "'");
    }
    return *value;
}

std::string RequiredString(const json::Value& object, const std::string& key) {
    return Required(object, key).AsString();
}

bool RequiredBool(const json::Value& object, const std::string& key) {
    return Required(object, key).AsBool();
}

std::uint32_t NumberToUInt32(const json::Value& value,
                             const std::string& field) {
    const double number = value.AsNumber();
    if (number < 0 || number > std::numeric_limits<std::uint32_t>::max() ||
        std::floor(number) != number) {
        throw json::Error("field '" + field + "' must be an unsigned integer");
    }
    return static_cast<std::uint32_t>(number);
}

std::uint32_t RequiredUInt32(const json::Value& object,
                             const std::string& key) {
    return NumberToUInt32(Required(object, key), key);
}

std::uint32_t ParseHash(const json::Value& value) {
    if (value.IsNumber()) {
        return NumberToUInt32(value, "modelHash");
    }
    const auto& text = value.AsString();
    if (text.empty()) {
        throw json::Error("field 'modelHash' cannot be empty");
    }
    std::size_t consumed = 0;
    unsigned long parsed = 0;
    try {
        parsed = std::stoul(text, &consumed, 0);
    } catch (const std::exception&) {
        throw json::Error("field 'modelHash' must be a 32-bit integer or hex string");
    }
    if (consumed != text.size() ||
        parsed > std::numeric_limits<std::uint32_t>::max()) {
        throw json::Error("field 'modelHash' must be a 32-bit integer or hex string");
    }
    return static_cast<std::uint32_t>(parsed);
}

std::optional<std::tuple<unsigned, unsigned, unsigned>>
ParseSemver(const std::string& value) {
    std::istringstream input(value);
    unsigned major = 0;
    unsigned minor = 0;
    unsigned patch = 0;
    char first_dot = 0;
    char second_dot = 0;
    if (!(input >> major >> first_dot >> minor >> second_dot >> patch) ||
        first_dot != '.' || second_dot != '.' || input.peek() != EOF) {
        return std::nullopt;
    }
    return std::make_tuple(major, minor, patch);
}

bool RuntimeSatisfies(const std::string& runtime,
                      const std::string& minimum) {
    const auto runtime_version = ParseSemver(runtime);
    const auto minimum_version = ParseSemver(minimum);
    return runtime_version.has_value() && minimum_version.has_value() &&
           *runtime_version >= *minimum_version;
}

bool IsRelativeSafePath(const std::string& value) {
    if (value.empty()) return false;
    const std::filesystem::path path(value);
    if (path.is_absolute() || path.has_root_name() || path.has_root_directory()) {
        return false;
    }
    return std::none_of(path.begin(), path.end(), [](const auto& part) {
        return part == "..";
    });
}

bool HasFatal(const std::vector<ValidationIssue>& issues,
              std::size_t first_new_issue) {
    return std::any_of(
        issues.begin() + static_cast<std::ptrdiff_t>(first_new_issue),
        issues.end(), [](const ValidationIssue& issue) { return issue.fatal; });
}

std::string ReadBoundedTextFile(const std::filesystem::path& path) {
    constexpr std::uintmax_t kMaximumConfigBytes = 1024U * 1024U;
    std::error_code error;
    const auto size = std::filesystem::file_size(path, error);
    if (error || size > kMaximumConfigBytes) {
        throw std::runtime_error("configuration is unreadable or exceeds 1 MiB");
    }
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("configuration could not be opened");
    }
    return std::string(std::istreambuf_iterator<char>(input),
                       std::istreambuf_iterator<char>());
}

std::string ModelHashText(std::uint32_t hash) {
    std::ostringstream output;
    output << "0x" << std::uppercase << std::hex << std::setw(8)
           << std::setfill('0') << hash;
    return output.str();
}

} // namespace

bool IsCanonicalPair(const std::string& left_bone,
                     const std::string& right_bone) noexcept {
    return std::any_of(kCanonicalPairs.begin(), kCanonicalPairs.end(),
                       [&](const CanonicalPair& pair) {
                           return left_bone == pair.first &&
                                  right_bone == pair.second;
                       });
}

std::vector<ValidationIssue>
ValidateConfiguration(const AxleConfiguration& configuration,
                      const std::string& runtime_version,
                      const std::string& source_name) {
    std::vector<ValidationIssue> issues;
    if (configuration.schema_version != kAxleSchemaVersion) {
        AddIssue(issues, "unsupported-schema",
                 "Configuration schema is newer or older than runtime schema 1",
                 source_name);
    }
    if (configuration.configuration_id.empty() ||
        configuration.configuration_id.size() > 128) {
        AddIssue(issues, "invalid-configuration-id",
                 "configurationId must contain 1 to 128 characters", source_name);
    }
    if (configuration.model_name.empty() ||
        configuration.model_name.size() > 128) {
        AddIssue(issues, "invalid-model-name",
                 "modelName must contain 1 to 128 characters", source_name);
    }
    if (configuration.model_hash == 0) {
        AddIssue(issues, "invalid-model-hash", "modelHash cannot be zero",
                 source_name);
    }
    if (!ParseSemver(configuration.minimum_runtime_version).has_value()) {
        AddIssue(issues, "invalid-minimum-runtime",
                 "minimumRuntimeVersion must be semantic version major.minor.patch",
                 source_name);
    } else if (!RuntimeSatisfies(runtime_version,
                                 configuration.minimum_runtime_version)) {
        AddIssue(issues, "runtime-too-old",
                 "Configuration requires runtime " +
                     configuration.minimum_runtime_version + " or newer",
                 source_name);
    }

    const std::size_t axle_count = configuration.axles.size();
    if (axle_count < 2 || axle_count > 5) {
        AddIssue(issues, "unsupported-physical-axle-count",
                 "Story axle configurations require 2 to 5 physical axle pairs; "
                 "extra wheels must remain cosmetic",
                 source_name);
    }
    if (configuration.expected_wheel_count != axle_count * 2U ||
        configuration.expected_wheel_count < 4 ||
        configuration.expected_wheel_count > 10) {
        AddIssue(issues, "invalid-wheel-count",
                 "expectedWheelCount must equal two physical wheels per axle "
                 "and be between 4 and 10",
                 source_name);
    }

    std::set<std::uint32_t> orders;
    std::set<std::string> configured_bones;
    for (std::size_t position = 0; position < axle_count; ++position) {
        const auto& axle = configuration.axles[position];
        if (!orders.insert(axle.order).second || axle.order != position) {
            AddIssue(issues, "invalid-axle-order",
                     "Axle order must be unique, contiguous, and match physical "
                     "front-to-rear array order",
                     source_name);
        }
        if (!IsCanonicalPair(axle.left_bone, axle.right_bone)) {
            AddIssue(issues, "invalid-canonical-pair",
                     "Axle " + std::to_string(position + 1) +
                         " is not a canonical left/right GTA wheel-bone pair",
                     source_name);
        }
        const bool valid_role = axle.role == "front" || axle.role == "middle" ||
                                axle.role == "rear" || axle.role == "tag";
        if (!valid_role || (position == 0 && axle.role != "front") ||
            (position + 1 == axle_count && axle.role != "rear") ||
            (position > 0 && position + 1 < axle_count &&
             axle.role != "middle" && axle.role != "tag")) {
            AddIssue(issues, "invalid-axle-role",
                     "Axle role must match its physical front/middle/tag/rear "
                     "position",
                     source_name);
        }
        if (axle_count >= 2 && position == 0 &&
            (axle.left_bone != "wheel_lf" || axle.right_bone != "wheel_rf")) {
            AddIssue(issues, "missing-front-pair",
                     "The first physical axle must use wheel_lf / wheel_rf",
                     source_name);
        }
        if (axle_count >= 2 && position + 1 == axle_count &&
            (axle.left_bone != "wheel_lr" || axle.right_bone != "wheel_rr")) {
            AddIssue(issues, "missing-rear-pair",
                     "The final physical axle must use wheel_lr / wheel_rr",
                     source_name);
        }
        if (position > 0 && position + 1 < axle_count) {
            const auto& expected = kCanonicalPairs[position];
            if (axle.left_bone != expected.first ||
                axle.right_bone != expected.second) {
                AddIssue(issues, "noncanonical-middle-order",
                         "Middle axle pairs must use lm1, lm2, and lm3 in "
                         "front-to-rear order",
                         source_name);
            }
        }
        configured_bones.insert(axle.left_bone);
        configured_bones.insert(axle.right_bone);
    }

    if (configuration.wheel_index_map.size() != configured_bones.size()) {
        AddIssue(issues, "incomplete-wheel-index-map",
                 "wheelIndexMap must contain exactly one entry for every "
                 "configured physical wheel bone; visual dual tyres are excluded",
                 source_name);
    }
    std::set<std::uint32_t> indices;
    for (const auto& [bone, index] : configuration.wheel_index_map) {
        if (configured_bones.find(bone) == configured_bones.end()) {
            AddIssue(issues, "unexpected-wheel-index-bone",
                     "wheelIndexMap contains unconfigured or cosmetic bone '" +
                         bone + "'",
                     source_name);
        }
        if (index >= configuration.expected_wheel_count) {
            AddIssue(issues, "wheel-index-out-of-range",
                     "Wheel index for '" + bone + "' is outside the expected "
                     "game wheel count",
                     source_name);
        }
        if (!indices.insert(index).second) {
            AddIssue(issues, "duplicate-wheel-index",
                     "wheelIndexMap contains duplicate physical wheel index " +
                         std::to_string(index),
                     source_name);
        }
    }
    for (const auto& bone : configured_bones) {
        if (configuration.wheel_index_map.find(bone) ==
            configuration.wheel_index_map.end()) {
            AddIssue(issues, "missing-wheel-index",
                     "wheelIndexMap has no target mapping for canonical bone '" +
                         bone + "'",
                     source_name);
        }
    }
    return issues;
}

std::optional<AxleConfiguration>
ParseConfigurationJson(const std::string& json_text,
                       const std::string& runtime_version,
                       std::vector<ValidationIssue>& issues,
                       const std::string& source_name) {
    const auto first_issue = issues.size();
    try {
        const auto root = json::Parse(json_text);
        root.AsObject();
        AxleConfiguration result;
        const auto serialized_schema = RequiredUInt32(root, "schemaVersion");
        if (serialized_schema > kAxleSchemaVersion) {
            AddIssue(issues, "unsupported-schema",
                     "Configuration requires a newer axle schema", source_name);
            return std::nullopt;
        }
        const bool migrate_v0 = serialized_schema == 0;
        result.schema_version = kAxleSchemaVersion;
        result.configuration_id = RequiredString(root, "configurationId");
        result.model_name = RequiredString(root, "modelName");
        result.model_hash = ParseHash(Required(root, "modelHash"));
        result.expected_wheel_count = RequiredUInt32(root, "expectedWheelCount");
        if (const auto* minimum = root.Find("minimumRuntimeVersion")) {
            result.minimum_runtime_version = minimum->AsString();
        } else if (migrate_v0) {
            result.minimum_runtime_version = "1.0.0";
        } else {
            throw json::Error("missing required field 'minimumRuntimeVersion'");
        }

        if (const auto* mapping_value = root.Find("wheelIndexMap")) {
            const auto& mapping = mapping_value->AsObject();
            for (const auto& [bone, index] : mapping) {
                result.wheel_index_map.emplace(
                    bone, NumberToUInt32(index, "wheelIndexMap." + bone));
            }
        } else if (const auto* mapping_value = root.Find("wheelIndexMapping")) {
            const auto& mapping = Required(*mapping_value, "by_bone").AsObject();
            for (const auto& [bone, index] : mapping) {
                result.wheel_index_map.emplace(
                    bone, NumberToUInt32(index,
                                         "wheelIndexMapping.by_bone." + bone));
            }
        }

        const auto& axles = Required(root, "axles").AsArray();
        result.axles.reserve(axles.size());
        for (const auto& value : axles) {
            value.AsObject();
            AxleDefinition axle;
            axle.order = RequiredUInt32(value, "order");
            axle.role = RequiredString(value, "role");
            axle.left_bone = RequiredString(value, "leftBone");
            axle.right_bone = RequiredString(value, "rightBone");
            axle.steered = RequiredBool(value, "steered");
            axle.powered = RequiredBool(value, "powered");
            if (const auto* indices_value = value.Find("wheelIndices")) {
                const auto& indices = indices_value->AsArray();
                if (indices.size() != 2) {
                    throw json::Error(
                        "wheelIndices must contain left and right indices");
                }
                const auto left_index =
                    NumberToUInt32(indices[0], "wheelIndices[0]");
                const auto right_index =
                    NumberToUInt32(indices[1], "wheelIndices[1]");
                const auto left_existing =
                    result.wheel_index_map.find(axle.left_bone);
                const auto right_existing =
                    result.wheel_index_map.find(axle.right_bone);
                if ((left_existing != result.wheel_index_map.end() &&
                     left_existing->second != left_index) ||
                    (right_existing != result.wheel_index_map.end() &&
                     right_existing->second != right_index)) {
                    throw json::Error(
                        "per-axle wheelIndices conflict with wheelIndexMapping");
                }
                result.wheel_index_map.emplace(axle.left_bone, left_index);
                result.wheel_index_map.emplace(axle.right_bone, right_index);
            } else if (migrate_v0) {
                throw json::Error(
                    "schema-0 axle is missing required wheelIndices");
            }
            result.axles.push_back(std::move(axle));
        }

        if (result.wheel_index_map.empty()) {
            throw json::Error(
                "configuration requires wheelIndexMapping or per-axle wheelIndices");
        }

        if (migrate_v0) {
            AddIssue(issues, "configuration-migrated-v0",
                     "Legacy schema 0 was migrated in memory to schema 1; save "
                     "the project to persist wheelIndexMap",
                     source_name, false);
        }

        if (const auto* compatibility = root.Find("compatibility")) {
            compatibility->AsObject();
            const bool has_explicit_story_targets =
                compatibility->Find("storyLegacy") != nullptr ||
                compatibility->Find("storyEnhanced") != nullptr ||
                compatibility->Find("story-legacy") != nullptr ||
                compatibility->Find("story-enhanced") != nullptr;
            if (has_explicit_story_targets) {
                result.story_legacy = false;
                result.story_enhanced = false;
            }
            if (const auto* legacy = compatibility->Find("storyLegacy")) {
                result.story_legacy = legacy->AsBool();
            }
            if (const auto* enhanced = compatibility->Find("storyEnhanced")) {
                result.story_enhanced = enhanced->AsBool();
            }
            if (const auto* legacy = compatibility->Find("story-legacy")) {
                result.story_legacy = legacy->AsBool();
            }
            if (const auto* enhanced = compatibility->Find("story-enhanced")) {
                result.story_enhanced = enhanced->AsBool();
            }
        }

        auto validation =
            ValidateConfiguration(result, runtime_version, source_name);
        issues.insert(issues.end(), std::make_move_iterator(validation.begin()),
                      std::make_move_iterator(validation.end()));
        if (HasFatal(issues, first_issue)) {
            return std::nullopt;
        }
        return result;
    } catch (const std::exception& error) {
        AddIssue(issues, "configuration-parse-failed", error.what(), source_name);
        return std::nullopt;
    }
}

ConfigurationCatalog
LoadConfigurationDirectory(const std::filesystem::path& directory,
                           const std::string& runtime_version) {
    ConfigurationCatalog catalog;
    std::error_code error;
    if (!std::filesystem::is_directory(directory, error) || error) {
        AddIssue(catalog.issues, "configuration-directory-unavailable",
                 "Configuration directory is unavailable", "configs");
        return catalog;
    }

    std::vector<std::filesystem::path> files;
    for (const auto& entry : std::filesystem::directory_iterator(directory, error)) {
        if (error) break;
        if (entry.is_regular_file(error) && !error &&
            entry.path().extension() == ".json") {
            files.push_back(entry.path());
        }
        error.clear();
    }
    std::sort(files.begin(), files.end());
    catalog.files_seen = files.size();

    std::vector<std::pair<std::string, AxleConfiguration>> parsed;
    for (const auto& path : files) {
        const auto source_name = path.filename().string();
        try {
            auto configuration = ParseConfigurationJson(
                ReadBoundedTextFile(path), runtime_version, catalog.issues,
                source_name);
            if (configuration.has_value()) {
                parsed.emplace_back(source_name, std::move(*configuration));
            }
        } catch (const std::exception& exception) {
            AddIssue(catalog.issues, "configuration-read-failed",
                     exception.what(), source_name);
        }
    }

    std::map<std::uint32_t, std::size_t> counts;
    for (const auto& item : parsed) {
        ++counts[item.second.model_hash];
    }
    for (auto& [source_name, configuration] : parsed) {
        if (counts[configuration.model_hash] != 1) {
            AddIssue(catalog.issues, "duplicate-model-hash",
                     "Conflicting configuration for model " +
                         ModelHashText(configuration.model_hash) +
                         " was disabled",
                     source_name);
            continue;
        }
        catalog.active.emplace(configuration.model_hash,
                               std::move(configuration));
    }
    return catalog;
}

std::optional<RuntimeSettings>
ParseRuntimeSettingsJson(const std::string& json_text,
                         std::vector<ValidationIssue>& issues,
                         const std::string& source_name) {
    const auto first_issue = issues.size();
    try {
        const auto root = json::Parse(json_text);
        root.AsObject();
        RuntimeSettings result;
        result.schema_version = RequiredUInt32(root, "schemaVersion");
        if (result.schema_version != kRuntimeSettingsSchemaVersion) {
            AddIssue(issues, "unsupported-runtime-settings-schema",
                     "runtime.json requires schema version 1", source_name);
            return std::nullopt;
        }
        if (const auto* value = root.Find("discoveryIntervalMs")) {
            result.discovery_interval_ms =
                NumberToUInt32(*value, "discoveryIntervalMs");
        }
        if (const auto* value = root.Find("recoveryIntervalMs")) {
            result.recovery_interval_ms =
                NumberToUInt32(*value, "recoveryIntervalMs");
        }
        if (const auto* value = root.Find("restoreOnUnload")) {
            result.restore_on_unload = value->AsBool();
        }
        if (const auto* value = root.Find("logFile")) {
            result.log_file = value->AsString();
        }
        if (result.discovery_interval_ms < 100 ||
            result.discovery_interval_ms > 10000) {
            AddIssue(issues, "invalid-discovery-interval",
                     "discoveryIntervalMs must be between 100 and 10000",
                     source_name);
        }
        if (result.recovery_interval_ms < result.discovery_interval_ms ||
            result.recovery_interval_ms > 60000) {
            AddIssue(issues, "invalid-recovery-interval",
                     "recoveryIntervalMs must be at least discoveryIntervalMs "
                     "and no more than 60000",
                     source_name);
        }
        if (!IsRelativeSafePath(result.log_file)) {
            AddIssue(issues, "unsafe-log-path",
                     "logFile must be a relative path below the runtime directory",
                     source_name);
        }
        if (std::any_of(
                issues.begin() + static_cast<std::ptrdiff_t>(first_issue),
                issues.end(), [](const auto& issue) {
                return issue.fatal;
            })) {
            return std::nullopt;
        }
        return result;
    } catch (const std::exception& error) {
        AddIssue(issues, "runtime-settings-parse-failed", error.what(),
                 source_name);
        return std::nullopt;
    }
}

} // namespace vwa
