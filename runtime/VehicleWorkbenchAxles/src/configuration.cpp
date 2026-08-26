#include "vehicle_workbench_axles/configuration.hpp"

#include "json.hpp"

#include <algorithm>
#include <array>
#include <cctype>
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

bool IsSha256(const std::string& value) {
    return value.size() == 64U && std::all_of(
        value.begin(), value.end(), [](unsigned char character) {
            return std::isxdigit(character) != 0;
        });
}

double EffectiveSteeringGain(const AxleDefinition& axle) noexcept {
    return axle.steering_gain.value_or(axle.steered ? 1.0 : 0.0);
}

bool IsLegacySteeringGain(const AxleDefinition& axle) noexcept {
    const double legacy = axle.steered ? 1.0 : 0.0;
    return std::abs(EffectiveSteeringGain(axle) - legacy) <=
           kSteeringGainEpsilon;
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
    if (configuration.schema_version < kLegacyAxleSchemaVersion ||
        configuration.schema_version > kAxleSchemaVersion) {
        AddIssue(issues, "unsupported-schema",
                 "Configuration schema is outside the supported 1-4 range",
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
    if (configuration.schema_version == kSignedSteeringAxleSchemaVersion &&
        !RuntimeSatisfies(configuration.minimum_runtime_version,
                          kSignedSteeringMinimumRuntime)) {
        AddIssue(issues, "signed-runtime-version-too-old",
                 "Schema 2 requires minimumRuntimeVersion 2.0.0 or newer",
                 source_name);
    }
    if (configuration.schema_version == kAxleSupportAxleSchemaVersion &&
        !RuntimeSatisfies(configuration.minimum_runtime_version,
                          kAxleSupportMinimumRuntime)) {
        AddIssue(issues, "support-runtime-version-too-old",
                 "Schema 3 support bias requires minimumRuntimeVersion 3.0.0 or newer",
                 source_name);
    }
    if (configuration.steering_command_polarity != "normal" &&
        configuration.steering_command_polarity != "inverted") {
        AddIssue(issues, "invalid-steering-command-polarity",
                 "steeringCommandPolarity must be normal or inverted",
                 source_name);
    }
    if (configuration.schema_version == kAxleSchemaVersion) {
        if (configuration.steering_command_polarity != "inverted") {
            AddIssue(issues, "schema-4-polarity-required",
                     "Schema 4 requires steeringCommandPolarity inverted",
                     source_name);
        }
        if (!RuntimeSatisfies(configuration.minimum_runtime_version,
                              kSteeringPolarityMinimumRuntime)) {
            AddIssue(issues, "polarity-runtime-version-too-old",
                     "Schema 4 inverted steering requires minimumRuntimeVersion 4.0.0 or newer",
                     source_name);
        }
    } else if (configuration.steering_command_polarity != "normal") {
        AddIssue(issues, "polarity-schema-mismatch",
                 "Inverted steering command polarity requires schema 4",
                 source_name);
    }
    if (!configuration.story_legacy && !configuration.story_enhanced) {
        AddIssue(issues, "no-story-compatibility",
                 "compatibility must explicitly enable storyLegacy, "
                 "storyEnhanced, story-legacy, or story-enhanced",
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

    bool layout_override_valid = false;
    if (configuration.intentional_layout_override.has_value()) {
        const auto& layout = *configuration.intentional_layout_override;
        bool valid = true;
        if (!RuntimeSatisfies(configuration.minimum_runtime_version,
                              kIntentionalLayoutMinimumRuntime)) {
            AddIssue(issues, "layout-override-runtime-version-too-old",
                     "intentionalLayoutOverride requires minimumRuntimeVersion 2.1.0 or newer",
                     source_name);
            valid = false;
        }
        if (layout.mode != "visual_instancing_remap") {
            AddIssue(issues, "invalid-layout-override-mode",
                     "intentionalLayoutOverride mode must be visual_instancing_remap",
                     source_name);
            valid = false;
        }
        if (!IsSha256(layout.bone_position_sha256)) {
            AddIssue(issues, "invalid-layout-override-digest",
                     "intentionalLayoutOverride requires a valid wheel-position SHA-256",
                     source_name);
            valid = false;
        }
        if (layout.reason.size() < 8U || layout.reason.size() > 240U ||
            layout.reason.find_first_of("\r\n") != std::string::npos) {
            AddIssue(issues, "invalid-layout-override-reason",
                     "intentionalLayoutOverride reason must be a single line of 8 to 240 characters",
                     source_name);
            valid = false;
        }
        if (layout.physical_bone_pairs.size() != axle_count) {
            AddIssue(issues, "incomplete-layout-override",
                     "intentionalLayoutOverride must map every physical axle pair exactly once",
                     source_name);
            valid = false;
        } else {
            std::set<std::pair<std::string, std::string>> unique_pairs;
            for (std::size_t position = 0; position < axle_count; ++position) {
                const auto& pair = layout.physical_bone_pairs[position];
                if (!IsCanonicalPair(pair.first, pair.second) ||
                    !unique_pairs.insert(pair).second) {
                    AddIssue(issues, "invalid-layout-override-pair",
                             "intentionalLayoutOverride contains a duplicate or noncanonical pair",
                             source_name);
                    valid = false;
                }
                if (pair.first != configuration.axles[position].left_bone ||
                    pair.second != configuration.axles[position].right_bone) {
                    AddIssue(issues, "layout-override-order-mismatch",
                             "intentionalLayoutOverride no longer matches the axle array order",
                             source_name);
                    valid = false;
                }
            }
        }
        if (valid) {
            bool canonical_order = true;
            for (std::size_t position = 0; position < axle_count; ++position) {
                const auto& expected = position + 1U == axle_count
                    ? kCanonicalPairs.back() : kCanonicalPairs[position];
                const auto& actual = layout.physical_bone_pairs[position];
                canonical_order = canonical_order &&
                    actual.first == expected.first && actual.second == expected.second;
            }
            if (canonical_order) {
                AddIssue(issues, "unnecessary-layout-override",
                         "intentionalLayoutOverride is only valid for a noncanonical physical order",
                         source_name);
                valid = false;
            }
        }
        layout_override_valid = valid;
    }

    std::set<std::uint32_t> orders;
    std::set<std::string> configured_bones;
    std::size_t suspension_count = 0;
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
        const double steering_gain = EffectiveSteeringGain(axle);
        if (!std::isfinite(steering_gain) ||
            steering_gain < kMinimumSteeringGain ||
            steering_gain > kMaximumSteeringGain) {
            AddIssue(issues, "invalid-steering-gain",
                     "Axle " + std::to_string(position + 1) +
                         " steeringGain must be finite and between -1 and 1",
                     source_name);
        } else if (!axle.steered &&
                   std::abs(steering_gain) > kSteeringGainEpsilon) {
            AddIssue(issues, "nonsteered-axle-gain",
                     "Axle " + std::to_string(position + 1) +
                         " must use steeringGain 0 when steered is false",
                     source_name);
        }
        if (configuration.schema_version == kLegacyAxleSchemaVersion &&
            !IsLegacySteeringGain(axle)) {
            AddIssue(issues, "schema-1-signed-steering-gain",
                     "Schema 1 permits legacy +1/0 steering only", source_name);
        }
        if (configuration.schema_version >= kSignedSteeringAxleSchemaVersion &&
            !axle.steering_gain.has_value()) {
            AddIssue(issues, "missing-explicit-steering-gain",
                     "Schemas 2 and 3 require steeringGain on every axle", source_name);
        }
        if (axle.suspension.has_value()) {
            ++suspension_count;
            const double support_weight = axle.suspension->support_weight;
            if (!std::isfinite(support_weight) ||
                support_weight < kMinimumSupportWeight ||
                support_weight > kMaximumSupportWeight) {
                AddIssue(issues, "invalid-support-weight",
                         "Axle " + std::to_string(position + 1) +
                             " suspension supportWeight must be finite and between 0.75 and 1.25",
                         source_name);
            }
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
        if (!layout_override_valid && axle_count >= 2 && position == 0 &&
            (axle.left_bone != "wheel_lf" || axle.right_bone != "wheel_rf")) {
            AddIssue(issues, "missing-front-pair",
                     "The first physical axle must use wheel_lf / wheel_rf",
                     source_name);
        }
        if (!layout_override_valid && axle_count >= 2 && position + 1 == axle_count &&
            (axle.left_bone != "wheel_lr" || axle.right_bone != "wheel_rr")) {
            AddIssue(issues, "missing-rear-pair",
                     "The final physical axle must use wheel_lr / wheel_rr",
                     source_name);
        }
        if (!layout_override_valid && position > 0 && position + 1 < axle_count) {
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

    if (configuration.schema_version == kAxleSupportAxleSchemaVersion) {
        if (suspension_count != axle_count) {
            AddIssue(issues, "incomplete-support-bias",
                     "Schema 3 requires suspension supportWeight on every physical axle",
                     source_name);
        }
    } else if (configuration.schema_version == kAxleSchemaVersion) {
        if (suspension_count != 0U && suspension_count != axle_count) {
            AddIssue(issues, "incomplete-support-bias",
                     "Schema 4 suspension supportWeight must cover every physical axle",
                     source_name);
        }
    } else if (suspension_count != 0U) {
        AddIssue(issues, "support-bias-schema-mismatch",
                 "Suspension supportWeight requires schema 3", source_name);
    }

    const bool has_nonlegacy_gain = std::any_of(
        configuration.axles.begin(), configuration.axles.end(),
        [](const AxleDefinition& axle) { return !IsLegacySteeringGain(axle); });
    if (configuration.schema_version == kLegacyAxleSchemaVersion &&
        configuration.steering_calculation.has_value()) {
        AddIssue(issues, "schema-1-steering-evidence",
                 "Schema 1 cannot contain steeringCalculation evidence",
                 source_name);
    }
    if (configuration.schema_version == kSignedSteeringAxleSchemaVersion) {
        if (!has_nonlegacy_gain) {
            AddIssue(issues, "schema-2-legacy-steering",
                     "Schema 2 is reserved for signed or scaled steering",
                     source_name);
        }
    }
    const bool requires_steering_evidence =
        configuration.schema_version >= kSignedSteeringAxleSchemaVersion &&
        has_nonlegacy_gain;
    if (!requires_steering_evidence &&
        configuration.schema_version != kLegacyAxleSchemaVersion &&
        configuration.steering_calculation.has_value()) {
        AddIssue(issues, "unnecessary-steering-evidence",
                 "steeringCalculation is only valid when signed or scaled steering is authored",
                 source_name);
    }
    if (requires_steering_evidence) {
        if (!configuration.steering_calculation.has_value()) {
            AddIssue(issues, "schema-2-missing-steering-evidence",
                     "Signed or scaled steering requires steeringCalculation evidence",
                     source_name);
        } else {
            const auto& evidence = *configuration.steering_calculation;
            if (evidence.mode != "automaticGeometry" &&
                evidence.mode != "manual") {
                AddIssue(issues, "invalid-steering-evidence-mode",
                         "steeringCalculation mode must be automaticGeometry or manual",
                         source_name);
            }
            if (evidence.algorithm_version != 1U) {
                AddIssue(issues, "invalid-steering-algorithm",
                         "steeringCalculation algorithmVersion must be 1",
                         source_name);
            }
            if (!IsSha256(evidence.bone_position_sha256)) {
                AddIssue(issues, "invalid-steering-evidence-digest",
                         "steeringCalculation bonePositionSha256 is invalid",
                         source_name);
            }
            if (configuration.intentional_layout_override.has_value()) {
                if (evidence.physical_bone_pairs !=
                    configuration.intentional_layout_override
                        ->physical_bone_pairs) {
                    AddIssue(issues, "steering-layout-evidence-mismatch",
                             "steeringCalculation physicalBonePairs must exactly match intentionalLayoutOverride and axle order",
                             source_name);
                }
                if (evidence.bone_position_sha256 !=
                    configuration.intentional_layout_override
                        ->bone_position_sha256) {
                    AddIssue(issues, "steering-layout-digest-mismatch",
                             "steeringCalculation and intentionalLayoutOverride must reference the same wheel-position digest",
                             source_name);
                }
            } else if (!evidence.physical_bone_pairs.empty()) {
                AddIssue(issues, "unexpected-steering-layout-evidence",
                         "steeringCalculation physicalBonePairs requires intentionalLayoutOverride",
                         source_name);
            }
            if (evidence.mode == "manual") {
                if (evidence.pivot_longitudinal_position.has_value() ||
                    !evidence.pivot_source.empty() ||
                    !evidence.pivot_axle_orders.empty() ||
                    evidence.reference_axle_order.has_value() ||
                    evidence.reference_lock_degrees.has_value() ||
                    evidence.pair_position_tolerance.has_value() ||
                    evidence.position_epsilon.has_value()) {
                    AddIssue(issues, "manual-steering-evidence-fields",
                             "Manual steering evidence cannot contain automatic geometry fields",
                             source_name);
                }
            } else if (evidence.mode == "automaticGeometry") {
                const bool automatic_fields =
                    evidence.pivot_longitudinal_position.has_value() &&
                    evidence.reference_axle_order.has_value() &&
                    evidence.reference_lock_degrees.has_value() &&
                    evidence.pair_position_tolerance.has_value() &&
                    evidence.position_epsilon.has_value() &&
                    std::isfinite(*evidence.pivot_longitudinal_position) &&
                    std::isfinite(*evidence.reference_lock_degrees) &&
                    std::isfinite(*evidence.pair_position_tolerance) &&
                    std::isfinite(*evidence.position_epsilon) &&
                    *evidence.reference_lock_degrees >= 1.0 &&
                    *evidence.reference_lock_degrees <= 80.0 &&
                    *evidence.pair_position_tolerance > 0.0 &&
                    *evidence.position_epsilon > 0.0;
                if (!automatic_fields) {
                    AddIssue(issues, "incomplete-automatic-steering-evidence",
                             "Automatic steering evidence requires finite pivot, reference, lock, pairPositionTolerance, and positionEpsilon fields",
                             source_name);
                }
                if (evidence.reference_axle_order.has_value() &&
                    (*evidence.reference_axle_order >= configuration.axles.size() ||
                     !configuration.axles[*evidence.reference_axle_order].steered)) {
                    AddIssue(issues, "invalid-steering-reference-axle",
                             "Automatic steering reference axle must exist and remain steered",
                             source_name);
                }
                const bool explicit_pivot = evidence.pivot_source == "explicit";
                const bool fixed_pivot =
                    evidence.pivot_source == "selected_fixed_axles" ||
                    evidence.pivot_source == "derived_fixed_axles";
                if (!explicit_pivot && !fixed_pivot) {
                    AddIssue(issues, "invalid-steering-pivot-source",
                             "Automatic steering pivotSource is invalid", source_name);
                }
                if ((explicit_pivot && !evidence.pivot_axle_orders.empty()) ||
                    (fixed_pivot && evidence.pivot_axle_orders.empty())) {
                    AddIssue(issues, "invalid-steering-pivot-selection",
                             "Automatic steering pivot axle selection conflicts with pivotSource",
                             source_name);
                }
                std::set<std::uint32_t> pivot_orders;
                for (const auto order : evidence.pivot_axle_orders) {
                    if (!pivot_orders.insert(order).second ||
                        order >= configuration.axles.size() ||
                        configuration.axles[order].steered) {
                        AddIssue(issues, "invalid-steering-pivot-axle",
                                 "Automatic steering pivot axles must be unique, existing fixed axles",
                                 source_name);
                    }
                }
            }
        }
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
        result.schema_version = migrate_v0
                                    ? kLegacyAxleSchemaVersion
                                    : serialized_schema;
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
        if (const auto* polarity = root.Find("steeringCommandPolarity")) {
            result.steering_command_polarity = polarity->AsString();
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
            if (const auto* gain = value.Find("steeringGain")) {
                axle.steering_gain = gain->AsNumber();
            }
            if (const auto* suspension = value.Find("suspension")) {
                suspension->AsObject();
                AxleSuspension settings;
                settings.support_weight =
                    Required(*suspension, "supportWeight").AsNumber();
                axle.suspension = settings;
            }
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

        if (const auto* calculation = root.Find("steeringCalculation")) {
            calculation->AsObject();
            SteeringCalculationEvidence evidence;
            evidence.mode = RequiredString(*calculation, "mode");
            evidence.algorithm_version = RequiredUInt32(
                *calculation, "algorithmVersion");
            evidence.bone_position_sha256 = RequiredString(
                *calculation, "bonePositionSha256");
            if (const auto* pivot =
                    calculation->Find("pivotLongitudinalPosition")) {
                evidence.pivot_longitudinal_position = pivot->AsNumber();
            }
            if (const auto* source = calculation->Find("pivotSource")) {
                evidence.pivot_source = source->AsString();
            }
            if (const auto* orders = calculation->Find("pivotAxleOrders")) {
                for (const auto& order : orders->AsArray()) {
                    evidence.pivot_axle_orders.push_back(
                        NumberToUInt32(order, "pivotAxleOrders"));
                }
            }
            if (const auto* reference =
                    calculation->Find("referenceAxleOrder")) {
                evidence.reference_axle_order = NumberToUInt32(
                    *reference, "referenceAxleOrder");
            }
            if (const auto* lock =
                    calculation->Find("referenceLockDegrees")) {
                evidence.reference_lock_degrees = lock->AsNumber();
            }
            if (const auto* tolerance =
                    calculation->Find("pairPositionTolerance")) {
                evidence.pair_position_tolerance = tolerance->AsNumber();
            }
            if (const auto* epsilon =
                    calculation->Find("positionEpsilon")) {
                evidence.position_epsilon = epsilon->AsNumber();
            }
            if (const auto* pairs =
                    calculation->Find("physicalBonePairs")) {
                for (const auto& pair_value : pairs->AsArray()) {
                    const auto& pair = pair_value.AsArray();
                    if (pair.size() != 2U) {
                        throw json::Error(
                            "steeringCalculation physicalBonePairs entries must contain left and right bones");
                    }
                    evidence.physical_bone_pairs.emplace_back(
                        pair[0].AsString(), pair[1].AsString());
                }
            }
            result.steering_calculation = std::move(evidence);
        }

        if (const auto* authored_override =
                root.Find("intentionalLayoutOverride")) {
            authored_override->AsObject();
            IntentionalLayoutOverride layout;
            layout.mode = RequiredString(*authored_override, "mode");
            layout.bone_position_sha256 = RequiredString(
                *authored_override, "bonePositionSha256");
            layout.reason = RequiredString(*authored_override, "reason");
            const auto& pairs = Required(
                *authored_override, "physicalBonePairs").AsArray();
            layout.physical_bone_pairs.reserve(pairs.size());
            for (const auto& pair_value : pairs) {
                const auto& pair = pair_value.AsArray();
                if (pair.size() != 2U) {
                    throw json::Error(
                        "intentionalLayoutOverride physicalBonePairs entries must contain left and right bones");
                }
                layout.physical_bone_pairs.emplace_back(
                    pair[0].AsString(), pair[1].AsString());
            }
            result.intentional_layout_override = std::move(layout);
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
            const auto& values = compatibility->AsObject();
            static const std::set<std::string> recognized{
                "storyLegacy", "storyEnhanced",
                "story-legacy", "story-enhanced",
            };
            for (const auto& [key, value] : values) {
                if (recognized.find(key) == recognized.end()) {
                    throw json::Error(
                        "compatibility contains unrecognized target '" + key +
                        "'");
                }
                value.AsBool();
            }

            const auto resolve_target = [&](const char* camel,
                                            const char* dashed) {
                const auto* camel_value = compatibility->Find(camel);
                const auto* dashed_value = compatibility->Find(dashed);
                if (camel_value != nullptr && dashed_value != nullptr &&
                    camel_value->AsBool() != dashed_value->AsBool()) {
                    throw json::Error(
                        std::string("compatibility aliases disagree for '") +
                        camel + "'");
                }
                return (camel_value != nullptr && camel_value->AsBool()) ||
                       (dashed_value != nullptr && dashed_value->AsBool());
            };
            result.story_legacy =
                resolve_target("storyLegacy", "story-legacy");
            result.story_enhanced =
                resolve_target("storyEnhanced", "story-enhanced");
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
